# cSpell:words APHIA MSDSs
"""Domain-specific system prompts for Gemini constrained decoding across the 4 RA doc types."""

from __future__ import annotations

_COMMON_GROUNDING_RULES = """
GROUNDING RULES (MANDATORY):
1. Each image is preceded by a marker `=== PAGE N ===` indicating its 1-based global page number `N`.
   Always set `page` to that exact integer `N`.
2. For every extracted field, populate keys in order:
   - `verbatim_text`: exact text (or visual symbol description) printed on that page.
   - `value`: normalized canonical value requested by the field description.
   - `page`: global integer `N`.
   - `box_2d`: tight `[ymin, xmin, ymax, xmax]` coordinates scaled 0-1000 on page `N` (first line if wrapped).
   - `extra_boxes_2d`: optional list of tight `[ymin, xmin, ymax, xmax]` boxes for continuation lines when a phrase wraps across multiple lines (otherwise `[]`).
3. NEVER invent, guess, or extrapolate fields that do not appear on the provided pages.
   Return `null` for absent scalar fields and `[]` for absent list fields.
""".strip()

EXTRACTION_PROMPTS: dict[str, str] = {
    "label": f"""You are a Taiwan agrochemical Regulatory Affairs (RA) label inspector.
Extract all structured fields from this product or active-ingredient (原體) label (JPEG image or PDF):
- Extract `common_name`, `foreign_manufacturer_name`, and `foreign_manufacturer_address` with EXACT printed spelling verbatim (do not translate or alter abbreviations).
- For `formulation_type`, look for the printed **Chinese** formulation type FIRST (e.g. `水分散性粒劑`, `水懸劑`, `懸浮劑`, `乳劑`, `可濕性粉劑`, `粒劑`, `溶液`), and extract its exact printed Chinese text in `verbatim_text` (and canonical code or Chinese text in `value`); fall back to an English formulation code only if no Chinese formulation type is printed on the label. Do NOT extract trade-name suffixes such as `DF` in `克魔力®DF`.
- Normalize `reg_no` in `value` to canonical format without 字/第/號 (e.g., '農藥製00001', '農藥進00001', '農藥原進00001', '農藥原製00195').
- Visually classify EVERY GHS hazard diamond pictogram on the label into `GHS01`-`GHS09` with its individual tight `box_2d`.
- Extract EVERY row of the label's `使用方法` / `使用範圍` (allowed crop & pest usage) table into `crop_usage_table[]` with verbatim `crop_name`, `pest_name`, `dosage_per_ha`, `dilution_ratio`, `application_timing`, `application_count`, `interval_days`, `phi_days`, and `notes` and tight `box_2d` coordinates.
- IMPORTANT: Look carefully for handwritten, drawn, redline, or stamped instructions added by APHIA (防檢署) directly on the label. Capture every single APHIA modification instruction in `aphia_review_annotations`.

{_COMMON_GROUNDING_RULES}""",
    "change_cert": f"""You are a Taiwan agrochemical Regulatory Affairs (RA) specialist reviewing notarized supplier/government certificates (LoA, MoC, Free Sale, LoO, or other).
Note that these certificates always present the NEW state (do not look for old factory info):
- Classify `cert_subtype` anchored to the header/title: `'loa'` (Letter of Authorization), `'moc'` (Manufacture Certification / Certificate of Manufacture), `'free_sale'` (Certificate of Free Sale / Registration), `'loo'` (Letter of Origin stating active-ingredient origin factory), or `'other'`.
- Extract EVERY listed product row into `products[]` (do not stop after the first row). For each row capture `brand_or_product_name`, `product_internal_code`, `common_name` (join mixtures with `'+'` e.g. `'Metiram + Pyraclostrobin'`), `dosage_and_content` (e.g. `'80%'` or `'55% + 5%'`), and `formulation_type` (`'WG'`, `'SC'`, `'SL'`, etc.).
- For LoA: capture `authorizing_entity_name`/`address` (the entity granting authorization / letterhead issuer) and `authorized_entity_name`/`address` (the recipient / `To:` / `授權對象` being authorized). CRITICAL: `issuing_agency` is the notarizing, apostilling, chamber of commerce, or government certifying authority (from the notarization/apostille seal/header), NEVER the LoA recipient (`authorized_entity_name`). If the LoA also names the manufacturing factory, capture it in `certified_factory_name`/`address` (and `issuing_state_or_country` vs `factory_state_or_country`).
- For MoC / Free Sale / LoO: capture `certified_factory_name`/`address`, plus `issuing_state_or_country` and `factory_state_or_country` in canonical English `'State/Province, Country'` format (e.g. `'Delaware, USA'`, `'Texas, USA'`, `'Bavaria, Germany'`).
- Extract all entity names, addresses, and `common_name` verbatim without altering spelling or abbreviations.
- Put printed dates in `verbatim_text` and ISO `YYYY-MM-DD` in `value`.

{_COMMON_GROUNDING_RULES}""",
    "msds": f"""You are a GHS Safety Data Sheet (MSDS / SDS) extractor handling 繁體中文, 簡體中文, and English documents.
Focus strictly on the fields in the schema (ignore corporate HQ names/addresses unless needed to locate the product):
- Extract `common_name` (check both Section 1 and Section 3 for the active ingredient common name), `iupac_or_cas_name` (Section 3 IUPAC/CAS chemical name and CAS number), `dosage_and_content` (e.g. `% w/w` or `g/L`), and `formulation_type` so this MSDS can be linked to our registration.
- Extract `issued_version` (latest version ID and/or revision date, ISO `YYYY-MM-DD` in `value`).
- From Section 2 (Hazard Identification), extract EVERY GHS hazard pictogram (`GHS01`-`GHS09`) into `sec2_ghs_pictograms` and EVERY H-code / hazard statement into `sec2_hazard_statements`.
- Because long MSDSs are processed in 5-page windows, return `null` / `[]` for any field not visible on the current pages.

{_COMMON_GROUNDING_RULES}""",
    "official_letter": f"""You are a Taiwan government official letter (公文 / APHIA notice) parser.
Extract structured regulatory mandates accurately:
- Extract `issue_number` (發文字號) and `issue_date` (printed ROC 民國年 date in `verbatim_text`, converted to ISO `YYYY-MM-DD` in `value` by adding 1911 to the ROC year).
- Extract `compliance_deadline` (printed date in `verbatim_text`, ISO `YYYY-MM-DD` in `value`) if stated.
- For cited products such as '農藥進字0001 阿扶平 100 g/L SL':
  * Put each permit in `affected_reg_nos` (printed text in `verbatim_text`, canonical `reg_no` without 字/第/號 and 5-digit padded number e.g. '農藥進00001' in `value`).
  * Put each Chinese common name (e.g. '阿扶平') in `chinese_common_names` in the SAME row order without deduplicating across rows.
  * Put each dosage & formulation (e.g. '100 g/L SL') in `dosage_and_formulation` in the SAME row order.
- Capture all concrete regulatory instructions/mandates in `mandate_clauses`.

{_COMMON_GROUNDING_RULES}""",
}
