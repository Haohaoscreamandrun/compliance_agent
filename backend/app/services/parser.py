# cSpell:words genai pymupdf
"""Interleaved page-image batching & Gemini constrained decoding parser (ADR-001 Decision 3)."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.db.models import VALID_DOC_TYPES
from app.prompts.extraction_prompts import EXTRACTION_PROMPTS
from app.schemas.extraction import SCHEMA_BY_DOC_TYPE
from app.schemas.grounded import GroundedField
from app.services.preprocessor import ProcessedPage

log = logging.getLogger(__name__)

MAX_SINGLE_CALL_PAGES = 8
WINDOW_SIZE_PAGES = 5

# Positional parallel arrays in OfficialLetterExtraction must NOT be deduplicated across rows
_NO_DEDUP_LIST_FIELDS = frozenset(
    {"affected_reg_nos", "chinese_common_names", "dosage_and_formulation"}
)


class GeminiParseError(RuntimeError):
    """Raised when Gemini returns an empty, blocked, or schema-invalid response."""


class ParseResult(NamedTuple):
    extraction: BaseModel
    model_used: str
    used_fallback: bool  # True when a 5xx/503 spike forced failover to a fallback model


def build_page_windows(
    pages: list[ProcessedPage],
    max_single_call_pages: int = MAX_SINGLE_CALL_PAGES,
    window_size: int = WINDOW_SIZE_PAGES,
) -> list[list[ProcessedPage]]:
    """Use 1 batch for <=8 pages; split >8-page PDFs into 5-page windows."""
    if not pages:
        raise ValueError("Cannot parse an empty page list.")
    if max_single_call_pages < 1 or window_size < 1:
        raise ValueError("max_single_call_pages and window_size must be >= 1.")
    if len(pages) <= max_single_call_pages:
        return [pages]
    return [pages[i : i + window_size] for i in range(0, len(pages), window_size)]


def build_interleaved_contents(
    doc_type: str,
    window_pages: list[ProcessedPage],
) -> list[Any]:
    """Build interleaved multimodal parts, using PyMuPDF digital text blocks for text-only MSDS pages."""
    if doc_type not in EXTRACTION_PROMPTS:
        raise ValueError(f"Unsupported doc_type: {doc_type!r}")
    contents: list[Any] = []
    for page in window_pages:
        contents.append(f"=== PAGE {page.page_number} ===")
        use_digital_text = doc_type == "msds" and bool(page.text_blocks_text)
        if use_digital_text and not page.has_images:
            # Pure digital MSDS page (no GHS icons/images): send PyMuPDF [0-1000] blocks only
            contents.append(
                "DIGITAL_TEXT_BLOCKS ([ymin, xmin, ymax, xmax] 0-1000 | text):\n"
                + page.text_blocks_text
            )
        else:
            contents.append(types.Part.from_bytes(data=page.webp_bytes, mime_type="image/webp"))
            if use_digital_text:
                # Hybrid page (e.g. MSDS Page 2 with GHS diamond icons): include both image + exact blocks
                contents.append(
                    "DIGITAL_TEXT_BLOCKS ([ymin, xmin, ymax, xmax] 0-1000 | text):\n"
                    + page.text_blocks_text
                )
    return contents


def _filter_to_window_bounds(
    model: BaseModel,
    schema_cls: type[BaseModel],
    valid_pages: set[int],
) -> BaseModel:
    """Drop any GroundedField whose `page` is not in the current window's exact page set."""
    cleaned: dict[str, Any] = {}
    for field_name in schema_cls.model_fields:
        val = getattr(model, field_name)
        if isinstance(val, GroundedField):
            cleaned[field_name] = val if val.page in valid_pages else None
        elif isinstance(val, list):
            kept: list[Any] = []
            for item in val:
                if isinstance(item, GroundedField):
                    if item.page in valid_pages:
                        kept.append(item)
                elif isinstance(item, BaseModel):
                    bounded_sub = _filter_to_window_bounds(item, type(item), valid_pages)
                    if any(getattr(bounded_sub, k) is not None for k in type(bounded_sub).model_fields):
                        kept.append(bounded_sub)
                else:
                    kept.append(item)
            cleaned[field_name] = kept
        else:
            cleaned[field_name] = val
    return schema_cls.model_validate(cleaned)


def merge_window_extractions(
    schema_cls: type[BaseModel],
    window_results: list[BaseModel],
) -> BaseModel:
    """Merge multi-window extractions deterministically (first valid non-null scalar wins)."""
    if not window_results:
        return schema_cls()

    merged: dict[str, Any] = {}
    for field_name in schema_cls.model_fields:
        is_list_field = any(isinstance(getattr(w, field_name), list) for w in window_results)
        if is_list_field:
            combined: list[Any] = []
            seen: set[tuple[int, tuple[int, ...], str]] = set()
            dedup_enabled = field_name not in _NO_DEDUP_LIST_FIELDS
            for win in window_results:
                for item in getattr(win, field_name) or []:
                    if dedup_enabled and isinstance(item, GroundedField):
                        dedup_key = (item.page, tuple(item.box_2d), item.verbatim_text)
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                    combined.append(item)
            merged[field_name] = combined
        else:
            chosen = None
            for win in window_results:
                val = getattr(win, field_name)
                if val is not None:
                    chosen = val
                    break
            merged[field_name] = chosen
    return schema_cls.model_validate(merged)


def extract_response_json(response: Any, schema_cls: type[BaseModel]) -> BaseModel:
    """Validate finish_reason and non-empty response text before Pydantic parsing."""
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = str(getattr(candidates[0], "finish_reason", "") or "")
        if any(bad in finish_reason.upper() for bad in ("SAFETY", "RECITATION", "MAX_TOKENS", "BLOCK")):
            raise GeminiParseError(f"Gemini generation halted with finish_reason={finish_reason!r}")

    raw_text = getattr(response, "text", None) or ""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise GeminiParseError("Gemini returned an empty response payload.")
    try:
        return schema_cls.model_validate_json(raw_text)
    except Exception as exc:
        raise GeminiParseError(f"Gemini response failed {schema_cls.__name__} validation: {exc}") from exc


_extract_response_json = extract_response_json


async def _generate_with_5xx_fallback(
    client: genai.Client,
    candidate_models: list[str],
    contents: list[Any],
    config: types.GenerateContentConfig,
) -> tuple[Any, str]:
    """Call Gemini, failing over to fallback models only on transient 5xx/503 ServerError."""
    last_error: Exception | None = None
    for idx, model in enumerate(candidate_models):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            return response, model
        except (genai_errors.ServerError, httpx.TimeoutException, TimeoutError) as exc:
            last_error = exc
            if idx + 1 < len(candidate_models):
                log.warning(
                    "Gemini model %s failed (%s); failing over to %s",
                    model,
                    exc,
                    candidate_models[idx + 1],
                )
    assert last_error is not None
    if isinstance(last_error, genai_errors.ServerError):
        raise last_error
    raise GeminiParseError(f"All Gemini models timed out: {last_error}") from last_error


async def parse_document_pages(
    client: genai.Client,
    model_name: str,
    doc_type: str,
    pages: list[ProcessedPage],
    fallback_models: list[str] | None = None,
) -> ParseResult:
    """Run interleaved Gemini constrained decoding across page windows with 503 failover."""
    if doc_type not in SCHEMA_BY_DOC_TYPE:
        raise ValueError(f"Unsupported doc_type: {doc_type!r}")
    schema_cls = SCHEMA_BY_DOC_TYPE[doc_type]
    windows = build_page_windows(pages)
    resolution = (
        types.MediaResolution.MEDIA_RESOLUTION_HIGH
        if doc_type == "label"
        else types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
    )
    candidate_models = list(dict.fromkeys([model_name, *(fallback_models or [])]))

    config = types.GenerateContentConfig(
        system_instruction=EXTRACTION_PROMPTS[doc_type],
        temperature=0.0,
        media_resolution=resolution,
        response_mime_type="application/json",
        response_json_schema=schema_cls.model_json_schema(),
    )

    window_results: list[BaseModel] = []
    models_used: list[str] = []
    for window in windows:
        contents = build_interleaved_contents(doc_type, window)
        response, used_model = await _generate_with_5xx_fallback(
            client, candidate_models, contents, config
        )
        models_used.append(used_model)
        parsed = _extract_response_json(response, schema_cls)
        valid_pages = {p.page_number for p in window}
        bounded = _filter_to_window_bounds(parsed, schema_cls=schema_cls, valid_pages=valid_pages)
        window_results.append(bounded)

    final_model = models_used[-1]
    used_fallback = any(m != model_name for m in models_used)
    return ParseResult(
        extraction=merge_window_extractions(schema_cls, window_results),
        model_used=final_model,
        used_fallback=used_fallback,
    )


def extract_clauses_for_db(
    doc_id: str,
    doc_type: str,
    reg_no: str | None,
    common_name: str | None,
    extraction: BaseModel,
) -> list[dict[str, Any]]:
    """Flatten all GroundedField entries into `DocumentClause` row dicts matching DB constraints."""
    if not doc_id or len(doc_id) > 64:
        raise ValueError("doc_id must be 1..64 characters.")
    if doc_type not in VALID_DOC_TYPES:
        raise ValueError(f"doc_type must be one of {VALID_DOC_TYPES}, got {doc_type!r}")
    expected_cls = SCHEMA_BY_DOC_TYPE[doc_type]
    if not isinstance(extraction, expected_cls):
        raise TypeError(f"Expected {expected_cls.__name__} for doc_type={doc_type!r}")
    if reg_no is not None and len(reg_no) > 32:
        raise ValueError("reg_no exceeds String(32) DB limit.")
    if common_name is not None and len(common_name) > 128:
        raise ValueError("common_name exceeds String(128) DB limit.")

    clauses: list[dict[str, Any]] = []

    def _append_clause(section_id: str, gf: GroundedField[Any]) -> None:
        if len(section_id) > 64:
            raise ValueError(f"section_id {section_id!r} exceeds String(64) DB limit.")
        if gf.page < 1 or len(gf.box_2d) != 4 or not gf.verbatim_text.strip():
            raise ValueError(f"Invalid GroundedField bounds in {section_id!r}.")
        clauses.append(
            {
                "doc_id": doc_id,
                "doc_type": doc_type,
                "reg_no": reg_no,
                "common_name": common_name,
                "page_number": gf.page,
                "section_id": section_id,
                "verbatim_text": gf.verbatim_text,
                "normalized_value": None if gf.value is None else str(gf.value),
                "box_2d": list(gf.box_2d),
            }
        )

    for field_name in type(extraction).model_fields:
        val = getattr(extraction, field_name)
        items = val if isinstance(val, list) else ([val] if isinstance(val, GroundedField) else [])
        for idx, item in enumerate(items, start=1):
            if isinstance(item, GroundedField):
                _append_clause(f"{field_name}_{idx}" if isinstance(val, list) else field_name, item)
            elif isinstance(item, BaseModel):
                for sub_name in type(item).model_fields:
                    sub_val = getattr(item, sub_name)
                    if isinstance(sub_val, GroundedField):
                        _append_clause(f"{field_name}_{idx}_{sub_name}", sub_val)

    return clauses
