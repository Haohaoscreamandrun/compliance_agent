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
    "label": f"""You are a Taiwan agrochemical Regulatory Affairs (RA) label inspector following Taiwan's 農藥標示管理辦法 (Agro-pesticide Labeling Management Regulations).
Extract all structured fields from this product or active-ingredient (原體) label (JPEG image or multi-page PDF):
- Front panel layout (主標示):
  - `reg_no`: extract from the permit header (e.g. `農藥進字第03968號`), normalizing `value` without 字/第/號 (e.g. `'農藥製00001'`, `'農藥進03968'`, `'農藥原進00001'`, `'農藥原製00195'`).
  - `product_chinese_name` vs `common_name`: Taiwan labels typically print the brand name (`廠牌名稱`, e.g. `八將軍`) above the parenthesized common name (`農藥普通名稱`, e.g. `（殺芬草）` -> value `'殺芬草'`). Extract `common_name` with EXACT printed spelling verbatim (do not translate).
  - `dosage_and_content` & `formulation_type`: check `含量劑型` (e.g. `70% 水分散性粒劑`). For `formulation_type`, look for the printed **Chinese** formulation type FIRST (e.g. `水分散性粒劑`, `水懸劑`, `懸浮劑`, `乳劑`, `可濕性粉劑`, `粒劑`, `溶液`), extracting its exact printed Chinese text in `verbatim_text` (and canonical code or Chinese text in `value`); fall back to an English formulation code only if no Chinese formulation type is printed. Do NOT extract trade-name suffixes such as `DF` in `克魔力®DF`.
  - `local_permit_holder_name` & `local_permit_holder_address`: extract from `名稱` / `地址` (許可證持有廠商). Do not confuse with `分裝工廠` (`repackaging_factory_name`/`address`).
  - `foreign_manufacturer_name` & `foreign_manufacturer_address`: extract from `※ 國外生產工廠` / `國外原製造廠` with EXACT printed spelling verbatim (do not translate or alter abbreviations), separating the company/plant name from the street/city/country address.
- GHS hazards & emergency contacts (危害標示與急救):
  - `ghs_pictograms`: visually classify EVERY red-bordered GHS hazard diamond pictogram into `GHS01`-`GHS09` with its individual tight `box_2d`. Ignore the bottom blue/colored FAO PPE/safety icon strip (危害防範圖式).
  - `signal_word`: extract `危險` or `警告` printed beneath the GHS diamonds.
  - `iupac_name`: extract from the `有效成分與其他成分之種類及含量` table (`有效成分(IUPAC名稱)`).
  - `hazard_statements`: extract from `危害警告訊息`. Taiwan labels often list Chinese hazard statements separated by `、` or newlines without H-codes (e.g. `吞食可能有害、對水生生物毒性非常大並具有長期持續影響`); split and capture EVERY statement verbatim.
  - `emergency_phone`: extract emergency/poison-control phone numbers from `預防中毒及解毒方法` (e.g. 臺北榮總 `(02)28717121`, 高醫 `(07)3162631`) or `儲藏及使用時應注意事項` (company 0800 emergency line). Do NOT extract the permit holder's general office phone or the `0800-085717` waste-container recycling hotline.
- Usage table & APHIA annotations (使用方法與審查註記):
  - Extract EVERY row of the `使用方法及其範圍` (`使用方法` / `使用範圍`) table into `crop_usage_table[]` with verbatim `crop_name`, `pest_name`, `dosage_per_ha`, `dilution_ratio`, `application_timing`, `application_count`, `interval_days`, `phi_days`, and `notes` (include `施藥方法` + `注意事項` in `notes` when both columns appear) with tight `box_2d` coordinates.
  - IMPORTANT: Look carefully for handwritten, drawn, redline, or stamped instructions added by APHIA (防檢署) directly on the label. Capture every single APHIA modification instruction in `aphia_review_annotations`.

{_COMMON_GROUNDING_RULES}""",
    "change_cert": f"""You are a Taiwan agrochemical Regulatory Affairs (RA) specialist reviewing notarized supplier/government certificates (LoA, MoC, Free Sale, LoO, or other).
Note that these certificates always present the NEW state (do not look for old factory info):
- SKIP COVER LEGALIZATION & BLANK PAGES: If a page is a Taiwan ROC Document Legalization sheet (`中華民國文件證明專用` / `REPUBLIC OF CHINA (TAIWAN) DOCUMENT LEGALIZATION` / TECO signature authentication), skip it completely — ignore ALL names, authorities, and dates on that page (it only authenticates the notary's signature). Also ignore blank or stamp-only back pages.
- Classify `cert_subtype` anchored to the main certificate title (usually Page 2): `'loa'` (`Letter of Authorization`), `'moc'` (`Manufacture Certification` / `Certificate of Manufacture` / `Herstellungsgenehmigung` / `Manufacturing License`), `'free_sale'` (`Certificate of Free Sale` / `Registration`), `'loo'` (`Letter of Origin` stating active-ingredient origin factory), or `'other'`.
- For LoA (`Letter of Authorization`):
  - Focus on the paragraph(s) immediately following the `Letter of Authorization` title. Ignore top-left government agency addressees (e.g. `Animal and Plant Health Inspection Agency` / `Ministry of Agriculture`).
  - `authorizing_entity_name` / `address`: the original registration holder / authorizing entity granting authorization (e.g. `Herewith we – <entity>, <address> - certify that ...`).
  - `authorized_entity_name` / `address`: the Taiwan authorized entity / importer (e.g. `certify that <entity> is duly authorized to act as a sole importer and distributor...`), which will be checked against the registration holder (`廠商名稱`) on the label.
  - `products[]`: extract EVERY listed product row (do not stop after the first row) with `brand_or_product_name`, `common_name` (join mixtures with `'+'` e.g. `'Metiram + Pyraclostrobin'`), `dosage_and_content` (e.g. `'70%'` or `'55% + 5%'`), `formulation_type` (`'WG'`, `'SC'`, `'SL'`, etc.), and optional `product_internal_code` (e.g. `'BAS 800 01H'`).
  - `certified_factory_name` / `address` & `factory_state_or_country`: the actual physical manufacturer / production plant (e.g. `produced in our own production plant, located at ...` or `manufactured by ...`), NOT the registration holder / HQ if they differ.
  - Notarization page (usually Page 3): extract ONLY `issuing_authority` and `issuing_state_or_country` (where the document is notarized, in canonical English `'State/Province, Country'` format e.g. `'Texas, USA'`, `'Rhineland-Palatinate, Germany'`). In the US, the notary state must be compared against the manufacturer's state (`factory_state_or_country`), not the registration holder's state.
- For MoC (`Herstellungsgenehmigung` / `Manufacturing License` / `Certificate of Manufacture`) / Free Sale / LoO:
  - Document layouts vary across jurisdictions; after skipping Page 1 legalization, focus on the manufacturer (`certified_factory_name`, `certified_factory_address`, `factory_state_or_country`) and `products[]` (including any attached `PRODUCT LIST as attachment` page).
  - When a document lists both a corporate headquarter (`Hersteller / headquarter`) and a production plant (`Adresse der Produktionsstätte / plant`), ALWAYS extract the physical **production plant** into `certified_factory_name` / `certified_factory_address` / `factory_state_or_country`.
  - For MoC `products[]`, prioritize `common_name`, `dosage_and_content`, and `formulation_type` (plus `brand_or_product_name`; `product_internal_code` is optional and unnecessary).
  - For `issuing_authority` and `issuing_state_or_country`: extract from the government authorization body / notarization or apostille seal page (canonical English `'State/Province, Country'`, NEVER the LoA recipient (`authorized_entity_name`), the TECO cover page, or the APHIA addressee).
- Extract all entity names, addresses, and `common_name` verbatim without altering spelling or abbreviations.
- Put the certificate body's printed date in `effective_date.verbatim_text` and ISO `YYYY-MM-DD` in `value` (never use the TECO legalization date).

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
