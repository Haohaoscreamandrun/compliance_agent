# cSpell:words APHIA MSDSs casefold
"""Deterministic compliance cascade diff engine across Label, MSDS, LoA/COR Cert, MOA, and 公文."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.db.models import MoaRegistry
from app.schemas.extraction import (
    AuthorizedProductItem,
    MsdsExtraction,
    NotarizedCertExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.schemas.grounded import GroundedField

DiffCategory = Literal[
    "factory_source_change",
    "entity_contact_update",
    "ghs_hazard_drift",
    "moa_registry_sync",
    "aphia_label_instruction",
    "official_letter_mandate",
    "common_name_mismatch",
]
ActionStatus = Literal[
    "must_update",
    "pending_moa_approval",
    "verify_warning",
]

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_REG_NO_EXTRACT = re.compile(r"(農藥(?:原進|原製|製|進))字?(?:第)?([0-9]+)(?:號)?")
_REG_NO_STRICT = re.compile(r"\A(農藥(?:原進|原製|製|進))字?(?:第)?([0-9]{1,8})(?:號)?\Z")
_H_CODE_GROUP_RE = re.compile(r"H([234]\d{2})(?:\s*[+/]\s*(\d{3}))+")
_H_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(H[234]\d{2}[A-Za-z]{0,2})(?![A-Za-z0-9])")
_EN_SEP_RE = re.compile(r"\s*(?:[+/;]|(?<!\d),(?!\d)|\band\b)\s*", re.IGNORECASE)
_TECH_SUFFIX_RE = re.compile(r"\s+(?:technical|tech\.?|tc)\s*$", re.IGNORECASE)
_TECH_FORMULATIONS = {"原體", "technical", "tech", "tech.", "tc", "tk"}
# Official mapping from Taiwan MOA 農藥標準規格準則 第二條附表一 (plus common label variants)
_ZH_FORMULATION_MAP = {
    "噴霧劑": "AE",
    "（其他）液劑": "AL",
    "其他液劑": "AL",
    "（其他）粉劑": "AP",
    "其他粉劑": "AP",
    "塊劑": "BR",
    "濃餌劑": "CB",
    "觸殺粉劑": "CP",
    "膠囊懸著劑": "CS",
    "水分散性乳劑": "DC",
    "粉劑": "DP",
    "種子處理粉劑": "DS",
    "片劑（直接使用）": "DT",
    "乳劑": "EC",
    "乳粒劑": "EG",
    "油基乳劑": "EO",
    "乳粉劑": "EP",
    "種子處理乳劑": "ES",
    "水基乳劑": "EW",
    "種子處理用水懸劑": "FS",
    "燻煙劑": "FU",
    "氣劑": "GA",
    "膠劑": "GD",
    "產氣劑": "GE",
    "乳膠劑": "GL",
    "粒劑": "GR",
    "軟膏劑": "GS",
    "溶膠劑": "GW",
    "熱霧劑": "HN",
    "固液混合包裝桶混劑": "KK",
    "液液混合包裝桶混劑": "KL",
    "冷霧劑": "KN",
    "種子處理液劑": "LS",
    "微乳劑": "ME",
    "水分散性油懸劑": "OD",
    "油懸劑": "OF",
    "油溶性液劑": "OL",
    "油分散性粉劑": "OP",
    "糊狀劑": "PA",
    "藥籤劑": "PR",
    "餌劑": "RB",
    "水懸劑": "SC",
    "懸浮劑": "SC",
    "直接噴施水懸劑": "SD",
    "濃懸乳劑": "SE",
    "水溶性粒劑": "SG",
    "溶液": "SL",
    "水劑": "SL",
    "擴佈油": "SO",
    "水溶性粉劑": "SP",
    "水溶性片劑": "ST",
    "超低容量懸浮劑": "SU",
    "片劑": "TB",
    "超低容量液劑": "UL",
    "蒸散劑": "VP",
    "水分散性粒劑": "WG",
    "可溼性粉劑": "WP",
    "可濕性粉劑": "WP",
    "種子處理水分散性粉劑": "WS",
    "水分散性片劑": "WT",
    "其他（未命名）劑型": "XX",
    "膠囊水懸混劑": "ZC",
    "膠囊懸乳混劑": "ZE",
    "膠囊水乳混劑": "ZW",
}


@dataclass(frozen=True, slots=True)
class FieldCitation:
    doc_id: str
    page: int
    box_2d: list[int]
    verbatim_quote: str
    extra_boxes_2d: list[list[int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CascadeDiffFinding:
    field_name: str
    category: DiffCategory
    action_status: ActionStatus
    current_label_value: str
    recommended_new_value: str
    rationale: str
    current_label_citation: FieldCitation | None = None
    supporting_citations: list[FieldCitation] = field(default_factory=list)


def normalize_reg_no(raw: str) -> str:
    """Normalize '農藥進字第０３２２５號' or '農藥進字003225' -> '農藥進03225'."""
    cleaned = re.sub(r"\s+", "", raw.translate(_FULLWIDTH_DIGITS))
    match = _REG_NO_EXTRACT.search(cleaned)
    if not match:
        return cleaned
    permit_type, digits = match.group(1), match.group(2)
    return f"{permit_type}{(digits.lstrip('0') or '0').zfill(5)}"


def validate_reg_no(raw: str) -> str:
    """Strictly validate and normalize a full Taiwan pesticide permit number."""
    cleaned = re.sub(r"\s+", "", raw.translate(_FULLWIDTH_DIGITS))
    match = _REG_NO_STRICT.fullmatch(cleaned)
    if not match:
        raise ValueError(f"Invalid Taiwan pesticide registration number: {raw!r}.")
    permit_type, digits = match.group(1), match.group(2)
    return f"{permit_type}{(digits.lstrip('0') or '0').zfill(5)}"


def _norm_ws(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _gf_str(gf: GroundedField[Any] | None) -> str:
    return _norm_ws(str(gf.value)) if gf is not None else ""


def _en_name_tokens(name: str, *, strip_tech: bool = False) -> set[str]:
    tokens: set[str] = set()
    for part in _EN_SEP_RE.split(_norm_ws(name)):
        cleaned = _TECH_SUFFIX_RE.sub("", part).strip() if strip_tech else part.strip()
        if cleaned:
            tokens.add(cleaned.casefold())
    return tokens


def _is_technical_permit(canon_reg: str, formulation: str | None = None) -> bool:
    return canon_reg.startswith("農藥原") or _norm_ws(formulation).casefold() in _TECH_FORMULATIONS


def _norm_formulation(val: str | None) -> str:
    cleaned = _norm_ws(val)
    if not cleaned:
        return ""
    if cleaned.casefold() in _TECH_FORMULATIONS:
        return "原體"
    if cleaned in _ZH_FORMULATION_MAP:
        return _ZH_FORMULATION_MAP[cleaned]
    return cleaned.upper()


def _norm_dosage(val: str | None) -> str:
    s = re.sub(r"\s+", "", (val or "").translate(_FULLWIDTH_DIGITS)).casefold()
    s = s.replace("(%)", "%")
    return re.sub(r"(\d+)\.0+(?=%|g/l|mg|kg|$)", r"\1", s)


def _norm_addr(val: str | None) -> str:
    s = _norm_ws(val).replace("ß", "ss").casefold()
    return re.sub(r"\bstr\.(?=\s|\d|$)", "strasse", s)


def _cite(doc_id: str, gf: GroundedField[Any] | None) -> FieldCitation | None:
    if gf is None:
        return None
    return FieldCitation(
        doc_id=doc_id,
        page=gf.page,
        box_2d=list(gf.box_2d),
        verbatim_quote=gf.verbatim_text,
        extra_boxes_2d=[list(b) for b in gf.extra_boxes_2d],
    )


def _cites(*pairs: tuple[str, GroundedField[Any] | None]) -> list[FieldCitation]:
    return [c for doc_id, gf in pairs if (c := _cite(doc_id, gf)) is not None]


def _expand_h_shorthand(text: str) -> str:
    # Expand 'H301+311+331' -> 'H301+H311+H331' before tokenizing individual codes.
    def _repl(match: re.Match[str]) -> str:
        parts = re.split(r"\s*[+/]\s*", match.group(0))
        return "+".join(p if p.upper().startswith("H") else f"H{p}" for p in parts)

    return _H_CODE_GROUP_RE.sub(_repl, text).replace("+", " ")


def _extract_h_codes(statements: list[GroundedField[str]]) -> dict[str, GroundedField[str]]:
    """Extract normalized H-codes from `stmt.value` first, falling back to `stmt.verbatim_text`."""
    codes: dict[str, GroundedField[str]] = {}
    for stmt in statements:
        found = _H_CODE_RE.findall(_expand_h_shorthand(str(stmt.value)))
        if not found:
            found = _H_CODE_RE.findall(_expand_h_shorthand(stmt.verbatim_text))
        for code in found:
            codes.setdefault(code.upper(), stmt)
    return codes


def _format_h_code_recommendation(code: str, stmt: GroundedField[str]) -> str:
    val = _norm_ws(str(stmt.value))
    if not val or val.upper() == code:
        verb = _norm_ws(stmt.verbatim_text)
        return verb if (verb and verb.upper() != code) else code
    if val.upper().startswith(code):
        return val
    return f"{code}: {val}"


def _validate_inputs(
    reg_no: str,
    label: ProductLabelExtraction | None,
    label_doc_id: str | None,
    msds: MsdsExtraction | None,
    msds_doc_id: str | None,
    change_cert: NotarizedCertExtraction | None,
    cert_doc_id: str | None,
    moa: MoaRegistry | None,
    official_letters: list[tuple[str, OfficialLetterExtraction]],
) -> tuple[str, str | None, str | None, str | None, list[tuple[str, OfficialLetterExtraction]]]:
    cleaned_reg = re.sub(r"\s+", "", reg_no.translate(_FULLWIDTH_DIGITS))
    if not _REG_NO_EXTRACT.search(cleaned_reg):
        raise ValueError(f"Invalid Taiwan pesticide registration number: {reg_no!r}.")
    canon_reg = normalize_reg_no(cleaned_reg)

    l_id = label_doc_id.strip() if label_doc_id else ""
    m_id = msds_doc_id.strip() if msds_doc_id else ""
    c_id = cert_doc_id.strip() if cert_doc_id else ""

    if label is not None and not l_id:
        raise ValueError("label_doc_id is required when label is provided.")
    if msds is not None and not m_id:
        raise ValueError("msds_doc_id is required when msds is provided.")
    if change_cert is not None and not c_id:
        raise ValueError("cert_doc_id is required when change_cert is provided.")

    stripped_letters: list[tuple[str, OfficialLetterExtraction]] = []
    for letter_doc_id, letter in official_letters:
        lid = letter_doc_id.strip() if letter_doc_id else ""
        if not lid:
            raise ValueError("Each official letter entry must include a non-empty doc_id.")
        stripped_letters.append((lid, letter))

    if moa is not None and normalize_reg_no(moa.reg_no) != canon_reg:
        raise ValueError(f"MOA record reg_no {moa.reg_no!r} does not match target {canon_reg!r}.")
    if label is not None and label.reg_no is not None:
        label_reg = normalize_reg_no(str(label.reg_no.value))
        if label_reg != canon_reg:
            raise ValueError(f"Label reg_no {label_reg!r} does not match target {canon_reg!r}.")

    return canon_reg, l_id or None, m_id or None, c_id or None, stripped_letters


def score_matching_cert_product(
    change_cert: NotarizedCertExtraction,
    canon_reg: str,
    label: ProductLabelExtraction | None,
    moa: MoaRegistry | None,
) -> tuple[AuthorizedProductItem | None, int]:
    """Score and select the product row matching the target permit; requires name or brand match."""
    if not change_cert.products:
        return None, 0
    label_cn = _gf_str(label.common_name if label else None)
    moa_cn = _norm_ws(moa.chinese_name) if moa else ""
    moa_en = _norm_ws(moa.english_name) if moa else ""
    moa_brand = _norm_ws(moa.brand_name) if moa else ""
    ref_form = _gf_str(label.formulation_type if label else None) or (_norm_ws(moa.formulation) if moa else "")
    ref_dosage = _gf_str(label.dosage_and_content if label else None) or (_norm_ws(moa.content_pct) if moa else "")
    is_tech = _is_technical_permit(canon_reg, ref_form)
    moa_tokens = _en_name_tokens(moa_en, strip_tech=is_tech)
    cn_targets = {n for n in (moa_cn, label_cn) if n}
    cn_token_targets = [_en_name_tokens(n) for n in cn_targets]

    if not (cn_targets or moa_en or moa_brand):
        return None, 0

    best_item: AuthorizedProductItem | None = None
    best_score = 0
    for item in change_cert.products:
        name_or_brand_matched = False
        score = 0
        item_cn = _gf_str(item.common_name)
        item_tokens = _en_name_tokens(item_cn, strip_tech=is_tech)
        if item_cn and (
            (item_tokens and any(item_tokens == t for t in cn_token_targets))
            or (item_tokens and moa_tokens and item_tokens == moa_tokens)
        ):
            score += 3
            name_or_brand_matched = True

        item_brand = _gf_str(item.brand_or_product_name)
        if item_brand and moa_brand and item_brand.casefold() == moa_brand.casefold():
            score += 2
            name_or_brand_matched = True

        if not name_or_brand_matched:
            continue

        item_form = _gf_str(item.formulation_type)
        if item_form and (ref_form or is_tech):
            target_form = _norm_formulation("原體" if is_tech and not ref_form else ref_form)
            if _norm_formulation(item_form) == target_form:
                score += 1
        item_dosage = _gf_str(item.dosage_and_content)
        if item_dosage and ref_dosage and _norm_dosage(item_dosage) == _norm_dosage(ref_dosage):
            score += 1
        if score > best_score:
            best_score = score
            best_item = item

    return best_item, best_score


def select_matching_cert_product(
    change_cert: NotarizedCertExtraction,
    canon_reg: str,
    label: ProductLabelExtraction | None,
    moa: MoaRegistry | None,
) -> AuthorizedProductItem | None:
    """Select the product row in a single- or multi-product certificate matching the target permit."""
    item, _ = score_matching_cert_product(change_cert, canon_reg, label, moa)
    return item


def _check_product_linkage(
    findings: list[CascadeDiffFinding],
    canon_reg: str,
    label: ProductLabelExtraction | None,
    label_doc_id: str | None,
    msds: MsdsExtraction | None,
    msds_doc_id: str | None,
    change_cert: NotarizedCertExtraction | None,
    cert_doc_id: str | None,
    moa: MoaRegistry | None,
) -> None:
    label_cn = _gf_str(label.common_name if label else None)
    msds_cn = _gf_str(msds.common_name if msds else None)
    moa_cn = _norm_ws(moa.chinese_name) if moa else ""
    moa_en = _norm_ws(moa.english_name) if moa else ""

    is_tech = _is_technical_permit(
        canon_reg,
        _gf_str(label.formulation_type if label else None) or (moa.formulation if moa else None),
    )

    if label is not None and label_doc_id and label_cn and (moa_cn or moa_en):
        label_matches_zh = bool(moa_cn and _en_name_tokens(label_cn) == _en_name_tokens(moa_cn))
        label_matches_en = bool(moa_en and _en_name_tokens(label_cn, strip_tech=is_tech) == _en_name_tokens(moa_en, strip_tech=is_tech))
        if not label_matches_zh and not label_matches_en:
            findings.append(
                CascadeDiffFinding(
                    field_name="農藥普通名稱 (中文 / 英文)",
                    category="common_name_mismatch",
                    action_status="must_update",
                    current_label_value=label_cn,
                    recommended_new_value=moa_cn or moa_en,
                    rationale=f"Label common name '{label_cn}' differs from MOA registered name '{moa_cn}' / '{moa_en}'.",
                    current_label_citation=_cite(label_doc_id, label.common_name),
                )
            )

    matched_cert_item: AuthorizedProductItem | None = None
    if change_cert is not None and cert_doc_id and change_cert.products:
        matched_cert_item = select_matching_cert_product(change_cert, canon_reg, label, moa)
        if matched_cert_item is None and (moa_en or moa_cn or label_cn):
            listed = ", ".join(
                _gf_str(p.common_name) or _gf_str(p.brand_or_product_name) or "(unnamed)"
                for p in change_cert.products
            )
            first_gf = next(
                (p.common_name or p.brand_or_product_name for p in change_cert.products if p.common_name or p.brand_or_product_name),
                None,
            )
            findings.append(
                CascadeDiffFinding(
                    field_name="農藥普通名稱 (英文 / Common Name)",
                    category="common_name_mismatch",
                    action_status="verify_warning",
                    current_label_value=label_cn or "(依中文標示)",
                    recommended_new_value=f"Cert: {listed} | MOA: {moa_en or moa_cn}",
                    rationale=f"Certificate products [{listed}] do not match permit '{canon_reg}' ({moa_en or moa_cn}).",
                    current_label_citation=_cite(label_doc_id, label.common_name) if (label and label_doc_id) else None,
                    supporting_citations=_cites((cert_doc_id, first_gf)),
                )
            )

    # MSDS product linkage: common_name, iupac_or_cas_name, formulation_type, dosage_and_content
    if msds is not None and msds_doc_id:
        if msds_cn and (moa_cn or moa_en or label_cn):
            cn_match = _en_name_tokens(msds_cn) in [_en_name_tokens(n) for n in (moa_cn, label_cn) if n]
            en_tokens = _en_name_tokens(msds_cn, strip_tech=is_tech)
            moa_tokens = _en_name_tokens(moa_en, strip_tech=is_tech)
            en_match = bool(en_tokens and moa_tokens and en_tokens == moa_tokens)
            if not cn_match and not en_match:
                findings.append(
                    CascadeDiffFinding(
                        field_name="MSDS 連結驗證 — 有效成分普通名稱",
                        category="common_name_mismatch",
                        action_status="verify_warning",
                        current_label_value=label_cn or moa_cn or moa_en,
                        recommended_new_value=msds_cn,
                        rationale=(
                            f"MSDS common name '{msds_cn}' does not match permit '{canon_reg}' "
                            f"(MOA: '{moa_cn}' / '{moa_en}'). Verify correct MSDS file."
                        ),
                        current_label_citation=_cite(label_doc_id, label.common_name) if (label and label_doc_id) else None,
                        supporting_citations=_cites((msds_doc_id, msds.common_name)),
                    )
                )

        msds_iupac = _gf_str(msds.iupac_or_cas_name)
        label_iupac = _gf_str(label.iupac_name if label else None)
        if msds_iupac and label_iupac:
            cas_re = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
            msds_cas = set(cas_re.findall(msds_iupac))
            label_cas = set(cas_re.findall(label_iupac))
            cas_overlap = bool(msds_cas and label_cas and (msds_cas & label_cas))
            name_overlap = _en_name_tokens(msds_iupac) == _en_name_tokens(label_iupac)
            if not cas_overlap and not name_overlap:
                findings.append(
                    CascadeDiffFinding(
                        field_name="MSDS 連結驗證 — 化學名稱 / CAS",
                        category="common_name_mismatch",
                        action_status="verify_warning",
                        current_label_value=label_iupac,
                        recommended_new_value=msds_iupac,
                        rationale=f"MSDS IUPAC/CAS '{msds_iupac}' differs from label IUPAC '{label_iupac}'.",
                        current_label_citation=_cite(label_doc_id, label.iupac_name) if (label and label_doc_id) else None,
                        supporting_citations=_cites((msds_doc_id, msds.iupac_or_cas_name)),
                    )
                )

    # Formulation & dosage linkage for MSDS and Change Cert (safe for 原體 formulation_type=None)
    ref_form = _gf_str(label.formulation_type if label else None) or (_norm_ws(moa.formulation) if moa else "")
    ref_dosage = _gf_str(label.dosage_and_content if label else None) or (_norm_ws(moa.content_pct) if moa else "")

    for src_name, src_doc_id, form_gf, dosage_gf in (
        ("MSDS", msds_doc_id, msds.formulation_type if msds else None, msds.dosage_and_content if msds else None),
        (
            "Cert",
            cert_doc_id,
            matched_cert_item.formulation_type if matched_cert_item else None,
            matched_cert_item.dosage_and_content if matched_cert_item else None,
        ),
    ):
        if not src_doc_id:
            continue
        src_form = _gf_str(form_gf)
        if src_form and (ref_form or is_tech):
            target_form = _norm_formulation("原體" if is_tech and not ref_form else ref_form)
            if _norm_formulation(src_form) != target_form:
                findings.append(
                    CascadeDiffFinding(
                        field_name=f"{src_name} 連結驗證 — 劑型 (Formulation)",
                        category="common_name_mismatch",
                        action_status="verify_warning",
                        current_label_value=ref_form or "原體",
                        recommended_new_value=src_form,
                        rationale=f"{src_name} formulation '{src_form}' differs from permit '{target_form}'.",
                        current_label_citation=_cite(label_doc_id, label.formulation_type) if (label and label_doc_id) else None,
                        supporting_citations=_cites((src_doc_id, form_gf)),
                    )
                )
        src_dosage = _gf_str(dosage_gf)
        if src_dosage and ref_dosage and _norm_dosage(src_dosage) != _norm_dosage(ref_dosage):
            findings.append(
                CascadeDiffFinding(
                    field_name=f"{src_name} 連結驗證 — 含量 / 規格",
                    category="common_name_mismatch",
                    action_status="verify_warning",
                    current_label_value=ref_dosage,
                    recommended_new_value=src_dosage,
                    rationale=f"{src_name} dosage/content '{src_dosage}' differs from permit '{ref_dosage}'.",
                    current_label_citation=_cite(label_doc_id, label.dosage_and_content) if (label and label_doc_id) else None,
                    supporting_citations=_cites((src_doc_id, dosage_gf)),
                )
            )


def _check_factory_and_moa(
    findings: list[CascadeDiffFinding],
    label: ProductLabelExtraction | None,
    label_doc_id: str | None,
    change_cert: NotarizedCertExtraction | None,
    cert_doc_id: str | None,
    moa: MoaRegistry | None,
) -> None:
    label_mfr_name = _gf_str(label.foreign_manufacturer_name if label else None)
    label_mfr_addr = _gf_str(label.foreign_manufacturer_address if label else None)
    moa_mfr_name = _norm_ws(moa.foreign_manufacturer) if moa else ""

    cert_mfr_name_gf = change_cert.certified_factory_name if change_cert else None
    cert_mfr_addr_gf = change_cert.certified_factory_address if change_cert else None
    cert_mfr_name = _gf_str(cert_mfr_name_gf)
    cert_mfr_addr = _gf_str(cert_mfr_addr_gf)

    if cert_mfr_name_gf is not None and cert_doc_id:
        moa_synced = bool(moa is not None and moa_mfr_name == cert_mfr_name)
        if label is not None and label_doc_id and label_mfr_name != cert_mfr_name:
            if moa is None:
                status: ActionStatus = "verify_warning"
                extra = " MOA registry unavailable to confirm approval."
            elif moa_synced:
                status = "must_update"
                extra = ""
            else:
                status = "pending_moa_approval"
                extra = f" MOA registry still lists '{moa_mfr_name or 'null'}' — permit amendment required before reprinting."
            findings.append(
                CascadeDiffFinding(
                    field_name="國外原製造廠名稱",
                    category="factory_source_change",
                    action_status=status,
                    current_label_value=label_mfr_name or "(未標示)",
                    recommended_new_value=cert_mfr_name,
                    rationale=f"Certificate factory '{cert_mfr_name}' differs from label '{label_mfr_name}'.{extra}",
                    current_label_citation=_cite(label_doc_id, label.foreign_manufacturer_name),
                    supporting_citations=_cites((cert_doc_id, cert_mfr_name_gf)),
                )
            )
        if moa is not None and not moa_synced:
            findings.append(
                CascadeDiffFinding(
                    field_name="MOA 登記國外原製造廠商同步狀態",
                    category="moa_registry_sync",
                    action_status="pending_moa_approval",
                    current_label_value=label_mfr_name or "(未提供現行標示)",
                    recommended_new_value=cert_mfr_name,
                    rationale=f"MOA EIR027 ({moa.reg_no}) lists '{moa_mfr_name or 'null'}', not certificate '{cert_mfr_name}'.",
                    current_label_citation=_cite(label_doc_id, label.foreign_manufacturer_name) if (label and label_doc_id) else None,
                    supporting_citations=_cites((cert_doc_id, cert_mfr_name_gf)),
                )
            )
    elif label is not None and label_doc_id and moa is not None and moa_mfr_name and label_mfr_name != moa_mfr_name:
        findings.append(
            CascadeDiffFinding(
                field_name="國外原製造廠名稱",
                category="factory_source_change",
                action_status="must_update",
                current_label_value=label_mfr_name or "(未標示)",
                recommended_new_value=moa_mfr_name,
                rationale=f"Label foreign manufacturer '{label_mfr_name}' differs from MOA registry '{moa_mfr_name}'.",
                current_label_citation=_cite(label_doc_id, label.foreign_manufacturer_name),
            )
        )

    if label is not None and label_doc_id and cert_mfr_addr_gf is not None and cert_doc_id:
        if _norm_addr(label_mfr_addr) != _norm_addr(cert_mfr_addr):
            if moa is None:
                addr_status: ActionStatus = "verify_warning"
            elif (cert_mfr_name and moa_mfr_name != cert_mfr_name) or (
                not cert_mfr_name and moa_mfr_name and label_mfr_name != moa_mfr_name
            ):
                addr_status = "pending_moa_approval"
            else:
                addr_status = "must_update"
            findings.append(
                CascadeDiffFinding(
                    field_name="國外原製造廠地址",
                    category="factory_source_change",
                    action_status=addr_status,
                    current_label_value=label_mfr_addr or "(未標示)",
                    recommended_new_value=cert_mfr_addr,
                    rationale=f"Factory address differs between certificate ('{cert_mfr_addr}') and label ('{label_mfr_addr}').",
                    current_label_citation=_cite(label_doc_id, label.foreign_manufacturer_address),
                    supporting_citations=_cites((cert_doc_id, cert_mfr_addr_gf)),
                )
            )

    if change_cert is not None and cert_doc_id and change_cert.issuing_state_or_country and change_cert.factory_state_or_country:
        issuing_loc = _gf_str(change_cert.issuing_state_or_country)
        factory_loc = _gf_str(change_cert.factory_state_or_country)
        if issuing_loc.casefold() != factory_loc.casefold():
            findings.append(
                CascadeDiffFinding(
                    field_name="證明文件簽發地與製造廠所在地差異",
                    category="factory_source_change",
                    action_status="verify_warning",
                    current_label_value=label_mfr_addr or "(依標示產地)",
                    recommended_new_value=f"Issuing: {issuing_loc} | Factory: {factory_loc}",
                    rationale=(
                        f"Issuing state/country ('{issuing_loc}') differs from physical factory state/country ('{factory_loc}'). "
                        "Verify legalization jurisdiction and origin statement."
                    ),
                    current_label_citation=_cite(label_doc_id, label.foreign_manufacturer_address) if (label and label_doc_id) else None,
                    supporting_citations=_cites(
                        (cert_doc_id, change_cert.issuing_state_or_country),
                        (cert_doc_id, change_cert.factory_state_or_country),
                    ),
                )
            )


def _entities_match(a: str, b: str, aliases: set[tuple[str, str]]) -> bool:
    if a == b:
        return True
    na = _norm_ws(a).casefold()
    nb = _norm_ws(b).casefold()
    return (na, nb) in aliases or (nb, na) in aliases


def _check_local_entity(
    findings: list[CascadeDiffFinding],
    label: ProductLabelExtraction | None,
    label_doc_id: str | None,
    change_cert: NotarizedCertExtraction | None,
    cert_doc_id: str | None,
    moa: MoaRegistry | None,
    confirmed_aliases: set[tuple[str, str]] | None = None,
) -> None:
    aliases = {(_norm_ws(z).casefold(), _norm_ws(e).casefold()) for z, e in (confirmed_aliases or ())}
    label_local = _gf_str(label.local_permit_holder_name if label else None)
    loa_local_gf = change_cert.authorized_entity_name if change_cert else None
    loa_local = _gf_str(loa_local_gf)
    moa_local = _norm_ws(moa.company_name) if moa else ""

    if label is not None and label_doc_id:
        if loa_local and not _entities_match(label_local, loa_local, aliases):
            if moa is None:
                status: ActionStatus = "verify_warning"
            elif _entities_match(moa_local, loa_local, aliases):
                status = "must_update"
            else:
                status = "pending_moa_approval"
            findings.append(
                CascadeDiffFinding(
                    field_name="國內許可證持有人 / 總代理名稱",
                    category="entity_contact_update",
                    action_status=status,
                    current_label_value=label_local or "(未標示)",
                    recommended_new_value=loa_local,
                    rationale=f"LoA authorized entity ('{loa_local}') differs from label holder ('{label_local}', MOA: '{moa_local or 'unavailable'}').",
                    current_label_citation=_cite(label_doc_id, label.local_permit_holder_name),
                    supporting_citations=_cites((cert_doc_id, loa_local_gf)) if cert_doc_id else [],
                )
            )
        if (
            moa_local
            and not _entities_match(label_local, moa_local, aliases)
            and not (
                loa_local
                and _entities_match(loa_local, moa_local, aliases)
                and not _entities_match(label_local, loa_local, aliases)
            )
        ):
            findings.append(
                CascadeDiffFinding(
                    field_name="國內許可證持有人 / 總代理名稱",
                    category="entity_contact_update",
                    action_status="must_update",
                    current_label_value=label_local or "(未標示)",
                    recommended_new_value=moa_local,
                    rationale=f"Label holder ('{label_local}') differs from MOA registered company ('{moa_local}').",
                    current_label_citation=_cite(label_doc_id, label.local_permit_holder_name),
                    supporting_citations=_cites((cert_doc_id, loa_local_gf))
                    if (cert_doc_id and _entities_match(loa_local, label_local, aliases))
                    else [],
                )
            )

    if loa_local and cert_doc_id and moa is not None and moa_local and not _entities_match(loa_local, moa_local, aliases):
        findings.append(
            CascadeDiffFinding(
                field_name="MOA 登記國內業者同步狀態",
                category="moa_registry_sync",
                action_status="pending_moa_approval",
                current_label_value=label_local or "(未提供現行標示)",
                recommended_new_value=loa_local,
                rationale=f"MOA EIR027 ({moa.reg_no}) lists '{moa_local}', not LoA authorized entity '{loa_local}'.",
                current_label_citation=_cite(label_doc_id, label.local_permit_holder_name) if (label and label_doc_id) else None,
                supporting_citations=_cites((cert_doc_id, loa_local_gf)),
            )
        )


def _check_msds_ghs_drift(
    findings: list[CascadeDiffFinding],
    label: ProductLabelExtraction | None,
    label_doc_id: str | None,
    msds: MsdsExtraction | None,
    msds_doc_id: str | None,
) -> None:
    if msds is None or not msds_doc_id:
        return
    if label is None or not label_doc_id:
        findings.append(
            CascadeDiffFinding(
                field_name="GHS 標示比對 (缺乏現行標籤)",
                category="ghs_hazard_drift",
                action_status="verify_warning",
                current_label_value="(未提供現行標示)",
                recommended_new_value=f"MSDS ({_gf_str(msds.issued_version) or 'latest'})",
                rationale="MSDS was provided without a product label; cannot verify GHS pictograms or hazard statements.",
                supporting_citations=_cites(
                    (msds_doc_id, msds.issued_version),
                    (msds_doc_id, msds.sec2_ghs_pictograms[0] if msds.sec2_ghs_pictograms else None),
                    (msds_doc_id, msds.sec2_hazard_statements[0] if msds.sec2_hazard_statements else None),
                ),
            )
        )
        return

    # 1. Pictograms must be identical between MSDS Section 2 and Label
    msds_pics = {p.value: p for p in msds.sec2_ghs_pictograms}
    label_pics_list = label.ghs_pictograms
    label_pic_codes = {p.value for p in label_pics_list}
    missing_on_label = sorted(set(msds_pics) - label_pic_codes)
    extra_on_label = sorted(label_pic_codes - set(msds_pics))

    if missing_on_label or extra_on_label:
        msds_ver = _gf_str(msds.issued_version) or "latest"
        extra_label_gf = next((p for p in label_pics_list if p.value in extra_on_label), None)
        label_cite_gf = extra_label_gf or (label_pics_list[0] if label_pics_list else None)
        findings.append(
            CascadeDiffFinding(
                field_name="GHS 危害圖式 (GHS01–GHS09)",
                category="ghs_hazard_drift",
                action_status="must_update",
                current_label_value=", ".join(sorted(label_pic_codes)) or "(無 / None)",
                recommended_new_value=", ".join(sorted(msds_pics)) or "(無 / None)",
                rationale=(
                    f"MSDS ({msds_ver}) Section 2 pictograms {sorted(msds_pics)} do not match label pictograms {sorted(label_pic_codes)}. "
                    f"Missing on label: {missing_on_label or 'none'}; extra on label: {extra_on_label or 'none'}."
                ),
                current_label_citation=_cite(label_doc_id, label_cite_gf),
                supporting_citations=_cites(
                    *((msds_doc_id, msds_pics[code]) for code in sorted(msds_pics)),
                    (msds_doc_id, msds.issued_version),
                ),
            )
        )

    # 2. Hazard Statements alignment (H-codes + Traditional Chinese / obsolete-label fallback)
    msds_hcodes = _extract_h_codes(msds.sec2_hazard_statements)
    label_hcodes = _extract_h_codes(label.hazard_statements)
    label_text = "; ".join(s.verbatim_text for s in label.hazard_statements)

    if msds_hcodes:
        missing_hcodes = sorted(set(msds_hcodes) - set(label_hcodes))
        extra_hcodes = sorted(set(label_hcodes) - set(msds_hcodes))
        if missing_hcodes or extra_hcodes:
            if not label.hazard_statements or label_hcodes:
                status: ActionStatus = "must_update"
            else:
                status = "verify_warning"
            extra_lbl_stmt = label_hcodes[extra_hcodes[0]] if extra_hcodes else None
            label_cite_gf = extra_lbl_stmt or (label.hazard_statements[0] if label.hazard_statements else None)
            findings.append(
                CascadeDiffFinding(
                    field_name="危害警告訊息 (GHS Hazard Statements)",
                    category="ghs_hazard_drift",
                    action_status=status,
                    current_label_value=label_text or "(未標示 / None)",
                    recommended_new_value="; ".join(
                        _format_h_code_recommendation(code, msds_hcodes[code]) for code in sorted(msds_hcodes)
                    ),
                    rationale=(
                        f"MSDS Section 2 H-codes {sorted(msds_hcodes)} vs label H-codes {sorted(label_hcodes)}. "
                        f"Missing: {missing_hcodes or 'none'}; extra on label: {extra_hcodes or 'none'}."
                    ),
                    current_label_citation=_cite(label_doc_id, label_cite_gf),
                    supporting_citations=_cites(
                        *((msds_doc_id, msds_hcodes[c]) for c in (missing_hcodes or sorted(msds_hcodes))),
                        (msds_doc_id, msds.issued_version),
                    ),
                )
            )
    elif msds.sec2_hazard_statements:
        msds_raw = "; ".join(str(s.value) for s in msds.sec2_hazard_statements)
        if not label.hazard_statements or _norm_ws(msds_raw) != _norm_ws(label_text):
            findings.append(
                CascadeDiffFinding(
                    field_name="危害警告訊息 (GHS Hazard Statements)",
                    category="ghs_hazard_drift",
                    action_status="verify_warning" if label.hazard_statements else "must_update",
                    current_label_value=label_text or "(未標示 / None)",
                    recommended_new_value=msds_raw,
                    rationale="Verify non-H-code MSDS Section 2 hazard statements against label text.",
                    current_label_citation=_cite(label_doc_id, label.hazard_statements[0] if label.hazard_statements else None),
                    supporting_citations=_cites(
                        (msds_doc_id, msds.sec2_hazard_statements[0]),
                        (msds_doc_id, msds.issued_version),
                    ),
                )
            )
    elif label.hazard_statements:
        findings.append(
            CascadeDiffFinding(
                field_name="危害警告訊息 (GHS Hazard Statements)",
                category="ghs_hazard_drift",
                action_status="must_update",
                current_label_value=label_text,
                recommended_new_value="(無 / None)",
                rationale="Label lists hazard statements, but latest MSDS Section 2 lists none.",
                current_label_citation=_cite(label_doc_id, label.hazard_statements[0]),
                supporting_citations=_cites((msds_doc_id, msds.issued_version)),
            )
        )


def _letter_dosage_matches_target(
    letter: OfficialLetterExtraction,
    canon_reg: str,
    label: ProductLabelExtraction | None,
    moa: MoaRegistry | None,
) -> bool:
    if not letter.dosage_and_formulation:
        return True
    is_tech = _is_technical_permit(
        canon_reg,
        _gf_str(label.formulation_type if label else None) or (moa.formulation if moa else None),
    )
    target_form = _norm_formulation(
        _gf_str(label.formulation_type if label else None) or (moa.formulation if moa else None)
    )
    target_dosage = _norm_dosage(
        _gf_str(label.dosage_and_content if label else None) or (moa.content_pct if moa else None)
    )
    for df_gf in letter.dosage_and_formulation:
        df_text = _gf_str(df_gf)
        if not df_text:
            continue
        if is_tech:
            if any(tok in df_text.casefold() for tok in ("原體", "technical", "tech")):
                return True
            continue
        norm_df = _norm_dosage(df_text)
        form_ok = not target_form or target_form == "原體" or target_form.casefold() in norm_df
        dosage_ok = not target_dosage or target_dosage in norm_df
        if form_ok and dosage_ok:
            return True
    return False


def _check_aphia_and_letters(
    findings: list[CascadeDiffFinding],
    canon_reg: str,
    label: ProductLabelExtraction | None,
    label_doc_id: str | None,
    msds: MsdsExtraction | None,
    official_letters: list[tuple[str, OfficialLetterExtraction]],
    moa: MoaRegistry | None,
) -> None:
    if label is not None and label_doc_id:
        for idx, ann in enumerate(label.aphia_review_annotations, start=1):
            findings.append(
                CascadeDiffFinding(
                    field_name=f"防檢署標籤審查修正指示 #{idx}",
                    category="aphia_label_instruction",
                    action_status="must_update",
                    current_label_value="(標示待修正區域)",
                    recommended_new_value=str(ann.value),
                    rationale=f"APHIA formal label annotation: {ann.verbatim_text}",
                    current_label_citation=_cite(label_doc_id, ann),
                )
            )

    known_cn_names = {
        n
        for n in (
            _gf_str(label.common_name if label else None),
            _gf_str(msds.common_name if msds else None),
            _norm_ws(moa.chinese_name) if moa else "",
        )
        if n
    }

    for letter_doc_id, letter in official_letters:
        letter_regs: set[str] = set()
        for r in letter.affected_reg_nos:
            combined = re.sub(r"\s+", "", f"{r.value} {r.verbatim_text}".translate(_FULLWIDTH_DIGITS))
            for m in _REG_NO_EXTRACT.finditer(combined):
                letter_regs.add(normalize_reg_no(m.group(0)))

        letter_names = {_norm_ws(str(n.value)) for n in letter.chinese_common_names if _norm_ws(str(n.value))}
        # Match by permit number first; only fall back to common_name + dosage/formulation when no permit numbers exist.
        matches_product = (
            (canon_reg in letter_regs)
            if letter_regs
            else (bool(known_cn_names & letter_names) and _letter_dosage_matches_target(letter, canon_reg, label, moa))
        )
        if not matches_product:
            continue

        issue_no = letter.issue_number.value if letter.issue_number else letter_doc_id
        deadline = f" (期限: {letter.compliance_deadline.value})" if letter.compliance_deadline else ""
        clauses = letter.mandate_clauses or ([letter.subject] if letter.subject else [])
        if not clauses:
            findings.append(
                CascadeDiffFinding(
                    field_name=f"官方公文應辦事項 ({issue_no})",
                    category="official_letter_mandate",
                    action_status="verify_warning",
                    current_label_value="(依現行標示檢查)",
                    recommended_new_value=f"查閱公文 {issue_no}{deadline}",
                    rationale=f"Official letter {issue_no} matched {canon_reg} but had no extracted mandate clauses or subject.",
                    supporting_citations=_cites(
                        (letter_doc_id, letter.issue_number),
                        (letter_doc_id, letter.affected_reg_nos[0] if letter.affected_reg_nos else None),
                        (letter_doc_id, letter.compliance_deadline),
                    ),
                )
            )
            continue

        for idx, clause in enumerate(clauses, start=1):
            findings.append(
                CascadeDiffFinding(
                    field_name=f"官方公文應辦事項 ({issue_no} #{idx})",
                    category="official_letter_mandate",
                    action_status="must_update",
                    current_label_value="(依現行標示檢查)",
                    recommended_new_value=f"{clause.value}{deadline}",
                    rationale=f"Official letter {issue_no} applies to {canon_reg}: {clause.verbatim_text}",
                    supporting_citations=_cites(
                        (letter_doc_id, clause),
                        (letter_doc_id, letter.issue_number),
                        (letter_doc_id, letter.compliance_deadline),
                    ),
                )
            )


def _check_cert_cross_consistency(
    findings: list[CascadeDiffFinding],
    active_certs: list[tuple[str, NotarizedCertExtraction]],
) -> None:
    """Verify that LoA and MoC/Free-Sale agree when multiple certs state a manufacturing factory."""
    named = [(cid, c, _gf_str(c.certified_factory_name)) for cid, c in active_certs if _gf_str(c.certified_factory_name)]
    if len(named) >= 2:
        names = {n for _, _, n in named}
        if len(names) > 1:
            findings.append(
                CascadeDiffFinding(
                    field_name="證明文件製造廠名稱一致性 (LoA vs MoC)",
                    category="factory_source_change",
                    action_status="verify_warning",
                    current_label_value=named[0][2],
                    recommended_new_value=" | ".join(f"{cid}: {n}" for cid, _, n in named),
                    rationale="Multiple certificates for this permit state different manufacturing factory names.",
                    supporting_citations=_cites(*((cid, c.certified_factory_name) for cid, c, _ in named)),
                )
            )
    addressed = [(cid, c, _gf_str(c.certified_factory_address)) for cid, c in active_certs if _gf_str(c.certified_factory_address)]
    if len(addressed) >= 2:
        addrs = {_norm_addr(a) for _, _, a in addressed}
        if len(addrs) > 1:
            findings.append(
                CascadeDiffFinding(
                    field_name="證明文件製造廠地址一致性 (LoA vs MoC)",
                    category="factory_source_change",
                    action_status="verify_warning",
                    current_label_value=addressed[0][2],
                    recommended_new_value=" | ".join(f"{cid}: {a}" for cid, _, a in addressed),
                    rationale="Multiple certificates for this permit state different manufacturing factory addresses.",
                    supporting_citations=_cites(*((cid, c.certified_factory_address) for cid, c, _ in addressed)),
                )
            )


def run_cascade_diff(
    *,
    reg_no: str,
    label: ProductLabelExtraction | None = None,
    label_doc_id: str | None = None,
    msds: MsdsExtraction | None = None,
    msds_doc_id: str | None = None,
    change_cert: NotarizedCertExtraction | None = None,
    cert_doc_id: str | None = None,
    extra_certs: list[tuple[str, NotarizedCertExtraction]] | None = None,
    moa: MoaRegistry | None = None,
    official_letters: list[tuple[str, OfficialLetterExtraction]] | None = None,
    confirmed_aliases: set[tuple[str, str]] | None = None,
) -> list[CascadeDiffFinding]:
    """Run deterministic compliance checks across all available regulatory sources for `reg_no`."""
    canon_reg, l_id, m_id, c_id, letters = _validate_inputs(
        reg_no,
        label,
        label_doc_id,
        msds,
        msds_doc_id,
        change_cert,
        cert_doc_id,
        moa,
        official_letters or [],
    )

    all_certs: list[tuple[str, NotarizedCertExtraction]] = []
    if change_cert is not None and c_id:
        all_certs.append((c_id, change_cert))
    for ec_id, ec in extra_certs or []:
        if not (ec_id or "").strip():
            raise ValueError("extra_certs entries must have non-empty doc_id.")
        all_certs.append((ec_id.strip(), ec))

    findings: list[CascadeDiffFinding] = []
    active_certs: list[tuple[str, NotarizedCertExtraction]] = []
    for cid, cert in all_certs:
        subtype = cert.cert_subtype.value if cert.cert_subtype else None
        if subtype == "loo":
            # Letter of Origin states active-ingredient origin factory (not printed on product label)
            continue
        if subtype == "other":
            title = _gf_str(cert.certificate_title) or cid
            clause_gf = cert.key_legal_clauses[0] if cert.key_legal_clauses else cert.cert_subtype
            findings.append(
                CascadeDiffFinding(
                    field_name=f"其他證明文件人工確認 ({title})",
                    category="factory_source_change",
                    action_status="verify_warning",
                    current_label_value="(依現行標示檢查)",
                    recommended_new_value=_gf_str(clause_gf) or title,
                    rationale=f"Unformatted 'other' certificate ({cid}) requires manual RA verification.",
                    supporting_citations=_cites((cid, clause_gf)),
                )
            )
            continue
        active_certs.append((cid, cert))

    if active_certs:
        for idx, (cid, cert) in enumerate(active_certs):
            _check_product_linkage(
                findings,
                canon_reg,
                label if idx == 0 else None,
                l_id if idx == 0 else None,
                msds if idx == 0 else None,
                m_id if idx == 0 else None,
                cert,
                cid,
                moa,
            )
            _check_factory_and_moa(findings, label, l_id, cert, cid, moa)
            _check_local_entity(findings, label, l_id, cert, cid, moa, confirmed_aliases)
        _check_cert_cross_consistency(findings, active_certs)
    else:
        _check_product_linkage(findings, canon_reg, label, l_id, msds, m_id, None, None, moa)
        _check_factory_and_moa(findings, label, l_id, None, None, moa)
        _check_local_entity(findings, label, l_id, None, None, moa, confirmed_aliases)

    _check_msds_ghs_drift(findings, label, l_id, msds, m_id)
    _check_aphia_and_letters(findings, canon_reg, label, l_id, msds, letters, moa)
    return findings
