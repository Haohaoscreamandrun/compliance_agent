# cSpell:words APHIA MSDSs Pydantic
"""Document-specific constrained decoding schemas for the 4 Taiwan RA document types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.grounded import GhsPictogramCode, GroundedField

CertSubtype = Literal["loa", "moc", "free_sale", "loo", "other"]


class ProductLabelExtraction(BaseModel):
    """Structured extraction for Traditional Chinese product & active-ingredient (原體) label images/PDFs."""

    reg_no: GroundedField[str] | None = Field(
        default=None,
        description="Taiwan permit number (canonical value without 字/第/號, e.g. 農藥製00001, 農藥進00001, 農藥原進00001, or 農藥原製00195).",
    )
    product_chinese_name: GroundedField[str] | None = Field(
        default=None,
        description="Product brand/trade name in Traditional Chinese as printed on the label.",
    )
    common_name: GroundedField[str] | None = Field(
        default=None,
        description="農藥普通名稱 / Active ingredient common name. Extract exact printed spelling verbatim; do not translate.",
    )
    iupac_name: GroundedField[str] | None = Field(
        default=None,
        description="IUPAC / chemical name of the active ingredient printed on the label (for linking to MSDS/MOA).",
    )
    dosage_and_content: GroundedField[str] | None = Field(
        default=None,
        description="Dosage, concentration, or active-ingredient purity e.g. 100 g/L, 75.000 (%), or 95% Tech.",
    )
    formulation_type: GroundedField[str] | None = Field(
        default=None,
        description="Formulation code e.g. SL, WP, EC (or null/Technical for active-ingredient 原體 permits).",
    )
    foreign_manufacturer_name: GroundedField[str] | None = Field(
        default=None,
        description="國外原製造廠名稱 / Origin factory name. Extract exact printed spelling verbatim; do not alter abbreviations.",
    )
    foreign_manufacturer_address: GroundedField[str] | None = Field(
        default=None,
        description="國外原製造廠地址 / Origin factory address. Extract exact printed address verbatim.",
    )
    local_permit_holder_name: GroundedField[str] | None = Field(
        default=None,
        description="廠商名稱 (國內許可證持有人/輸入商/製造商).",
    )
    local_permit_holder_address: GroundedField[str] | None = Field(
        default=None,
        description="Local permit holder/importer/manufacturer address as printed.",
    )
    repackaging_factory_name: GroundedField[str] | None = Field(
        default=None,
        description="分裝工廠名稱 (Local repackaging factory name if printed on the label).",
    )
    repackaging_factory_address: GroundedField[str] | None = Field(
        default=None,
        description="分裝工廠地址 (Local repackaging factory address if printed on the label).",
    )
    emergency_phone: GroundedField[str] | None = Field(
        default=None,
        description="Emergency or Poison Control contact phone number printed on the label.",
    )
    signal_word: GroundedField[str] | None = Field(
        default=None,
        description="警示語 e.g. 危險 / 警告.",
    )
    ghs_pictograms: list[GroundedField[GhsPictogramCode]] = Field(
        default_factory=list,
        description="Every GHS hazard diamond icon visible on the label mapped to GHS01-GHS09 with its individual box_2d.",
    )
    hazard_statements: list[GroundedField[str]] = Field(
        default_factory=list,
        description="危害警告訊息 (H-statements).",
    )
    aphia_review_annotations: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Formal modification instructions drawn, handwritten, or stamped by APHIA directly on the label.",
    )


class AuthorizedProductItem(BaseModel):
    """One authorized/registered product row inside a single- or multi-product certificate."""

    brand_or_product_name: GroundedField[str] | None = Field(
        default=None,
        description="Commercial brand or product name e.g. 'KUMULUS 80 DF' or 'CABRIO TOP'.",
    )
    product_internal_code: GroundedField[str] | None = Field(
        default=None,
        description="Chemical internal product code if stated e.g. 'BAS 175 01 F'.",
    )
    common_name: GroundedField[str] | None = Field(
        default=None,
        description="Active ingredient common name (for mixtures use '+', e.g. 'Metiram + Pyraclostrobin'). Extract exact printed spelling verbatim; do not translate.",
    )
    dosage_and_content: GroundedField[str] | None = Field(
        default=None,
        description="Dosage/concentration e.g. '80%', '100 g/L', or '55% + 5%' for mixtures.",
    )
    formulation_type: GroundedField[str] | None = Field(
        default=None,
        description="Formulation type e.g. SL, WP, EC, WG, SC.",
    )


class NotarizedCertExtraction(BaseModel):
    """Structured extraction for new-state LoA or Certificate of Registration documents."""

    cert_subtype: GroundedField[CertSubtype] | None = Field(
        default=None,
        description="'loa' (Letter of Authorization), 'moc' (Manufacture Certification), 'free_sale' (Certificate of Free Sale / Registration), 'loo' (Letter of Origin for active-ingredient origin), or 'other', anchored to the header/title.",
    )
    certificate_title: GroundedField[str] | None = Field(default=None)
    effective_date: GroundedField[str] | None = Field(
        default=None,
        description="Effective/issue date (printed text in verbatim_text; normalized ISO YYYY-MM-DD in value).",
    )

    # Issuing jurisdiction vs. factory jurisdiction (COR: highlight if issuing state != factory state)
    issuing_authority: GroundedField[str] | None = Field(
        default=None,
        description="Issuing state/country/department or notary.",
    )
    issuing_state_or_country: GroundedField[str] | None = Field(
        default=None,
        description="Canonical state/province and country of the issuing authority in English (e.g. 'Delaware, USA' or 'Bavaria, Germany').",
    )
    factory_state_or_country: GroundedField[str] | None = Field(
        default=None,
        description="Canonical state/province and country where the factory is physically located in English (e.g. 'Texas, USA' or 'Bavaria, Germany').",
    )

    # LoA: authorizing entity & authorized local entity (names + addresses)
    authorizing_entity_name: GroundedField[str] | None = Field(
        default=None,
        description="LoA: entity/manufacturer issuing the authorization. Extract exact printed spelling verbatim.",
    )
    authorizing_entity_address: GroundedField[str] | None = Field(
        default=None,
        description="LoA: full address of the authorizing entity verbatim.",
    )
    authorized_entity_name: GroundedField[str] | None = Field(
        default=None,
        description="LoA: local company/importer authorized to import or hold the permit. Extract exact printed spelling verbatim.",
    )
    authorized_entity_address: GroundedField[str] | None = Field(
        default=None,
        description="LoA: full address of the authorized local entity verbatim.",
    )

    # COR / Factory holder (extract verbatim for exact downstream comparison)
    certified_factory_name: GroundedField[str] | None = Field(
        default=None,
        description="Factory/entity holding the registration or production certificate. Extract exact printed spelling verbatim.",
    )
    certified_factory_address: GroundedField[str] | None = Field(
        default=None,
        description="Full physical address of the certified factory verbatim.",
    )

    # Product identification (supports single-product CORs and multi-product LoAs)
    products: list[AuthorizedProductItem] = Field(
        default_factory=list,
        description="Every authorized or registered product row listed in the certificate (do not stop after the first row).",
    )
    key_legal_clauses: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Operative clauses stating the authorization, registration, or factory/source facts.",
    )


class MsdsExtraction(BaseModel):
    """Focused extraction for 繁體中文 / 簡體中文 / English GHS MSDS PDFs (nullable for 5-page windows).

    Linked to a product registration via common_name + dosage_and_content + formulation_type;
    previous versions of the same MSDS are ignored in favor of the latest `issued_version`.
    """

    common_name: GroundedField[str] | None = Field(
        default=None,
        description="Active ingredient common name (繁體中文, 簡體中文, or English) used to link to registration.",
    )
    iupac_or_cas_name: GroundedField[str] | None = Field(
        default=None,
        description="Section 3 active ingredient IUPAC/CAS chemical name and CAS number (e.g. '139968-49-3') for linking to label/MOA.",
    )
    dosage_and_content: GroundedField[str] | None = Field(
        default=None,
        description="Dosage, concentration, or purity e.g. 100 g/L, 75%, or 95% Tech.",
    )
    formulation_type: GroundedField[str] | None = Field(
        default=None,
        description="Formulation type e.g. SL, WP, EC (or null/Technical for 原體).",
    )
    issued_version: GroundedField[str] | None = Field(
        default=None,
        description="Latest issued version ID and/or revision date (printed text in verbatim_text; normalized version/ISO YYYY-MM-DD in value).",
    )
    sec2_ghs_pictograms: list[GroundedField[GhsPictogramCode]] = Field(
        default_factory=list,
        description="Section 2 GHS hazard pictograms mapped to GHS01-GHS09 (must be identical to label).",
    )
    sec2_hazard_statements: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Section 2 GHS hazard statements (H-codes and hazard text; align with label).",
    )


class OfficialLetterExtraction(BaseModel):
    """Structured extraction for Taiwan government official letters (公文)."""

    issue_number: GroundedField[str] | None = Field(
        default=None,
        description="發文字號 e.g. 防檢二字第...",
    )
    issue_date: GroundedField[str] | None = Field(
        default=None,
        description="發文日期 (printed ROC date in verbatim_text; normalized ISO YYYY-MM-DD in value).",
    )
    subject: GroundedField[str] | None = Field(default=None, description="主旨.")
    compliance_deadline: GroundedField[str] | None = Field(
        default=None,
        description="Compliance deadline if stated (printed text in verbatim_text; normalized ISO YYYY-MM-DD in value).",
    )
    affected_reg_nos: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Permit numbers cited (printed text e.g. '農藥進字0001' in verbatim_text; canonical reg_no without 字/第/號 and 5-digit padded number e.g. '農藥進00001' in value).",
    )
    chinese_common_names: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Chinese common names cited (e.g. 阿扶平 from '農藥進字0001 阿扶平 100 g/L SL').",
    )
    dosage_and_formulation: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Dosage and formulation cited alongside the common name (e.g. '100 g/L SL').",
    )
    mandate_clauses: list[GroundedField[str]] = Field(
        default_factory=list,
        description="Specific regulatory mandates/instructions in 說明 with page and box_2d.",
    )


ExtractionModel = (
    ProductLabelExtraction
    | NotarizedCertExtraction
    | MsdsExtraction
    | OfficialLetterExtraction
)

SCHEMA_BY_DOC_TYPE: dict[str, type[BaseModel]] = {
    "label": ProductLabelExtraction,
    "msds": MsdsExtraction,
    "change_cert": NotarizedCertExtraction,
    "official_letter": OfficialLetterExtraction,
}
