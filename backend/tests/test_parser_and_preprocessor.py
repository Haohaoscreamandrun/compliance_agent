# cSpell:words pymupdf webp exif genai APHIA vertexai
"""Unit tests for CL 3.2: genai_client, in-RAM preprocessor, interleaved parser, & DB clauses."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image

from app.config import Settings
from app.schemas.extraction import (
    MsdsExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.services.genai_client import build_genai_client
from app.services.parser import (
    GeminiParseError,
    build_interleaved_contents,
    build_page_windows,
    extract_clauses_for_db,
    merge_window_extractions,
    parse_document_pages,
)
from app.services.preprocessor import ProcessedPage, preprocess_upload_in_memory


def _make_in_memory_pdf(num_pages: int, width: float = 600, height: float = 800) -> bytes:
    doc = pymupdf.open()
    for idx in range(1, num_pages + 1):
        page = doc.new_page(width=width, height=height)
        page.insert_text((72, 72), f"Synthetic Test Page {idx}")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_exif_rotated_jpeg(width: int = 2400, height: int = 1200) -> bytes:
    img = Image.new("RGB", (width, height), color=(220, 230, 240))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation = Rotate 90 CW -> transposed becomes 1200x2400
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_build_genai_client_handles_conflicting_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Even if GEMINI_API_KEY is in os.environ, Vertex AI mode must initialize cleanly
    sa_file = tmp_path / "vertex-sa.json"
    sa_file.write_text('{"type": "service_account"}', encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "stale-ai-studio-key")

    vertex_settings = Settings(
        _env_file=None,
        google_genai_use_vertexai=True,
        google_cloud_project="test-proj",
        google_cloud_location="asia-east1",
        secrets_dir=tmp_path,
        gcp_sa_key_filename="vertex-sa.json",
    )
    client = build_genai_client(vertex_settings)
    assert client.vertexai is True
    assert os.environ.get("GEMINI_API_KEY") == "stale-ai-studio-key"

    # Missing SA key file must fail fast
    bad_settings = Settings(
        _env_file=None,
        google_genai_use_vertexai=True,
        google_cloud_project="test-proj",
        secrets_dir=tmp_path,
        gcp_sa_key_filename="does-not-exist.json",
    )
    with pytest.raises(ValueError, match="not found"):
        build_genai_client(bad_settings)


def test_preprocess_pdf_in_memory_writes_only_webp_never_raw_pdf(tmp_path: Path) -> None:
    pdf_bytes = _make_in_memory_pdf(2)
    pages = preprocess_upload_in_memory(
        raw_bytes=pdf_bytes,
        filename="msds_sample.pdf",
        doc_id="msds_01",
        storage_dir=tmp_path,
        max_edge_px=1600,
    )
    assert len(pages) == 2
    assert [p.page_number for p in pages] == [1, 2]
    assert all(max(p.width_px, p.height_px) <= 1600 for p in pages)
    assert all((tmp_path / p.image_rel_path).is_file() for p in pages)
    assert list(tmp_path.rglob("*.pdf")) == []


def test_preprocess_giant_mediabox_pdf_scales_safely_without_oom(tmp_path: Path) -> None:
    # A 14400x14400 pt PDF MediaBox must be scaled to <= 1600px directly during rasterization
    pdf_bytes = _make_in_memory_pdf(1, width=14400, height=14400)
    pages = preprocess_upload_in_memory(
        raw_bytes=pdf_bytes,
        filename="huge_mediabox.pdf",
        doc_id="huge_01",
        storage_dir=tmp_path,
        max_edge_px=1600,
    )
    assert len(pages) == 1
    assert max(pages[0].width_px, pages[0].height_px) <= 1600


def test_preprocess_jpeg_exif_and_rgba_white_composite(tmp_path: Path) -> None:
    jpeg_bytes = _make_exif_rotated_jpeg(width=2400, height=1200)
    pages = preprocess_upload_in_memory(
        raw_bytes=jpeg_bytes,
        filename="label_photo.jpg",
        doc_id="label_01",
        storage_dir=tmp_path,
        max_edge_px=1600,
    )
    assert len(pages) == 1
    assert (pages[0].width_px, pages[0].height_px) == (800, 1600)
    assert list(tmp_path.rglob("*.jpg")) == []

    # Transparent PNG must composite onto white (255,255,255), not black (0,0,0)
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    buf = BytesIO()
    rgba.save(buf, format="PNG")
    png_pages = preprocess_upload_in_memory(
        raw_bytes=buf.getvalue(),
        filename="transparent_label.png",
        doc_id="label_png",
        storage_dir=tmp_path,
    )
    with Image.open(BytesIO(png_pages[0].webp_bytes)) as out_img:
        assert out_img.getpixel((50, 50)) == (255, 255, 255)


def test_preprocess_reupload_cleans_stale_pages_and_rejects_bad_inputs(tmp_path: Path) -> None:
    # First upload: 3 pages
    preprocess_upload_in_memory(_make_in_memory_pdf(3), "v1.pdf", "doc_reup", tmp_path)
    assert (tmp_path / "pages" / "doc_reup" / "page_3.webp").is_file()

    # Re-upload shorter 1-page doc on same doc_id: stale page_2/page_3 must be gone
    preprocess_upload_in_memory(_make_in_memory_pdf(1), "v2.pdf", "doc_reup", tmp_path)
    assert (tmp_path / "pages" / "doc_reup" / "page_1.webp").is_file()
    assert not (tmp_path / "pages" / "doc_reup" / "page_2.webp").exists()

    # Security & format guards
    for bad_id in ("../evil", "doc1\n", "CON", ""):
        with pytest.raises(ValueError, match="doc_id"):
            preprocess_upload_in_memory(b"data", "a.pdf", bad_id, tmp_path)

    with pytest.raises(ValueError, match="empty"):
        preprocess_upload_in_memory(b"", "a.pdf", "doc_1", tmp_path)

    with pytest.raises(ValueError, match="Unsupported file extension"):
        preprocess_upload_in_memory(b"hello", "no_ext", "doc_1", tmp_path)

    with pytest.raises(ValueError, match="corrupted PDF"):
        preprocess_upload_in_memory(b"not-a-pdf", "bad.pdf", "doc_1", tmp_path)


def test_build_page_windows_and_interleaved_contents() -> None:
    dummy_pages = [
        ProcessedPage(page_number=i, image_rel_path=f"pages/d/page_{i}.webp", width_px=800, height_px=1000, webp_bytes=b"RIFF")
        for i in range(1, 13)
    ]
    assert len(build_page_windows(dummy_pages[:8])) == 1
    windows = build_page_windows(dummy_pages)
    assert [[p.page_number for p in w] for w in windows] == [
        [1, 2, 3, 4, 5],
        [6, 7, 8, 9, 10],
        [11, 12],
    ]

    contents = build_interleaved_contents("msds", windows[1])
    assert "=== PAGE 6 ===" in contents
    assert "=== PAGE 10 ===" in contents
    assert "=== PAGE 1 ===" not in contents


def test_merge_window_extractions_preserves_official_letter_parallel_lists() -> None:
    # Two rows on different permits share identical common name '阿扶平'
    w1 = OfficialLetterExtraction(
        affected_reg_nos=[{"verbatim_text": "農藥進字0001", "value": "農藥進00001", "page": 1, "box_2d": [100, 50, 120, 200]}],
        chinese_common_names=[{"verbatim_text": "阿扶平", "value": "阿扶平", "page": 1, "box_2d": [100, 210, 120, 280]}],
        dosage_and_formulation=[{"verbatim_text": "100 g/L SL", "value": "100 g/L SL", "page": 1, "box_2d": [100, 290, 120, 380]}],
    )
    w2 = OfficialLetterExtraction(
        affected_reg_nos=[{"verbatim_text": "農藥進字0002", "value": "農藥進00002", "page": 6, "box_2d": [100, 50, 120, 200]}],
        chinese_common_names=[{"verbatim_text": "阿扶平", "value": "阿扶平", "page": 1, "box_2d": [100, 210, 120, 280]}],
        dosage_and_formulation=[{"verbatim_text": "200 g/L EC", "value": "200 g/L EC", "page": 6, "box_2d": [100, 290, 120, 380]}],
    )
    merged = merge_window_extractions(OfficialLetterExtraction, [w1, w2])
    assert isinstance(merged, OfficialLetterExtraction)
    assert len(merged.affected_reg_nos) == len(merged.chinese_common_names) == len(merged.dosage_and_formulation) == 2


async def test_parse_document_pages_filters_out_of_window_and_raises_on_blocked_response() -> None:
    pages = [
        ProcessedPage(page_number=i, image_rel_path=f"pages/d/page_{i}.webp", width_px=800, height_px=1000, webp_bytes=b"RIFF")
        for i in range(1, 11)
    ]
    configs: list[object] = []

    async def fake_generate_content(*, model, contents, config):
        configs.append(config)
        if "=== PAGE 1 ===" in contents:
            payload = MsdsExtraction(
                common_name={"verbatim_text": "Carbendazim", "value": "Carbendazim", "page": 1, "box_2d": [10, 10, 40, 200]},
                issued_version={"verbatim_text": "Fake", "value": "Fake", "page": 9, "box_2d": [10, 10, 40, 200]},
            )
        else:
            payload = MsdsExtraction(
                issued_version={"verbatim_text": "Rev 5.0", "value": "5.0", "page": 10, "box_2d": [20, 700, 50, 900]},
            )
        return SimpleNamespace(text=payload.model_dump_json(), candidates=[SimpleNamespace(finish_reason="STOP")])

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content)))
    result = await parse_document_pages(fake_client, "gemini-2.5-flash", "msds", pages)  # type: ignore[arg-type]

    assert len(configs) == 2
    assert getattr(configs[0], "system_instruction", None) is not None
    assert isinstance(result, MsdsExtraction)
    assert result.common_name is not None and result.common_name.value == "Carbendazim"
    assert result.issued_version is not None and result.issued_version.page == 10

    # Blocked or empty Gemini response must raise GeminiParseError, never return an empty model
    async def blocked_generate_content(*, model, contents, config):
        return SimpleNamespace(text="", candidates=[SimpleNamespace(finish_reason="SAFETY")])

    blocked_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=blocked_generate_content)))
    with pytest.raises(GeminiParseError, match="SAFETY"):
        await parse_document_pages(blocked_client, "gemini-2.5-flash", "label", pages[:1])  # type: ignore[arg-type]


def test_extract_clauses_for_db_flattens_and_validates_constraints() -> None:
    label = ProductLabelExtraction(
        reg_no={"verbatim_text": "農藥製字第00001號", "value": "農藥製00001", "page": 1, "box_2d": [10, 20, 40, 200]},
        aphia_review_annotations=[
            {"verbatim_text": "加註警語", "value": "Add warning", "page": 1, "box_2d": [300, 50, 350, 400]}
        ],
    )
    rows = extract_clauses_for_db("label_01", "label", "農藥製00001", "快得保淨", label)
    assert len(rows) == 2
    assert rows[0]["section_id"] == "reg_no"
    assert rows[1]["section_id"] == "aphia_review_annotations_1"

    with pytest.raises(ValueError, match="doc_type"):
        extract_clauses_for_db("label_01", "unknown_type", None, None, label)

    with pytest.raises(TypeError, match="ProductLabelExtraction"):
        extract_clauses_for_db("label_01", "label", None, None, MsdsExtraction())
