# cSpell:words ymin xmin ymax xmax genai APHIA
"""Unit tests for CL 3.1: GroundedField[T] Chain-of-Grounding and 4 extraction schemas."""

from __future__ import annotations

import pytest
from google.genai import Client, _transformers
from pydantic import ValidationError

from app.db.models import VALID_DOC_TYPES
from app.schemas.extraction import (
    SCHEMA_BY_DOC_TYPE,
    MsdsExtraction,
    NotarizedCertExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.schemas.grounded import GroundedField


def test_grounded_field_preserves_chain_of_grounding_field_order_and_bounds() -> None:
    expected_order = ["verbatim_text", "value", "page", "box_2d", "extra_boxes_2d"]
    assert list(GroundedField[str].model_fields.keys()) == expected_order

    json_schema = GroundedField[str].model_json_schema()
    assert list(json_schema["properties"].keys()) == expected_order
    # Ensure JSON Schema carries 0..1000 element bounds for Gemini constrained decoding
    box_items = json_schema["properties"]["box_2d"]["items"]
    assert box_items["minimum"] == 0
    assert box_items["maximum"] == 1000


def test_box_2d_clamps_jitter_and_swaps_inverted_axes() -> None:
    field = GroundedField[str](
        verbatim_text="ABC Chemical Co.",
        value="ABC Chemical Co.",
        page=1,
        box_2d=[300, 950, -10, 1020],  # inverted ymin/ymax + small boundary jitter
    )
    assert field.box_2d == [0, 950, 300, 1000]


@pytest.mark.parametrize(
    "bad_box",
    [
        [100, 100, 100, 200],         # zero height (ymin == ymax)
        [100, 200, 200, 200],         # zero width (xmin == xmax)
        [100, 200, 300],              # wrong length
        [100, 200, 300, 400, 500],
        [True, 10, 20, 30],           # bool is not a valid coordinate
        [float("inf"), 10, 20, 30],   # inf must raise ValidationError, not OverflowError
        [float("-inf"), 10, 20, 30],
        [float("nan"), 10, 20, 30],
        [150, 200, 1350, 1100],       # unnormalized pixel-scale coordinates must be rejected
        [-80, 10, 200, 300],
    ],
)
def test_box_2d_rejects_degenerate_or_malformed_boxes(bad_box: list[object]) -> None:
    with pytest.raises(ValidationError):
        GroundedField[str](
            verbatim_text="sample",
            value="sample",
            page=1,
            box_2d=bad_box,  # type: ignore[arg-type]
        )


def test_grounded_field_rejects_blank_verbatim_blank_value_or_zero_page() -> None:
    for bad_text in ("", "   ", "\n\t"):
        with pytest.raises(ValidationError):
            GroundedField[str](verbatim_text=bad_text, value="x", page=1, box_2d=[10, 10, 50, 50])
        with pytest.raises(ValidationError):
            GroundedField[str](verbatim_text="x", value=bad_text, page=1, box_2d=[10, 10, 50, 50])

    with pytest.raises(ValidationError):
        GroundedField[str](verbatim_text="x", value="x", page=0, box_2d=[10, 10, 50, 50])


def test_schema_by_doc_type_matches_db_valid_doc_types() -> None:
    assert set(SCHEMA_BY_DOC_TYPE.keys()) == set(VALID_DOC_TYPES)


def test_windowed_schemas_allow_empty_partial_pages_without_hallucination() -> None:
    empty_msds = MsdsExtraction()
    assert empty_msds.common_name is None
    assert empty_msds.issued_version is None
    assert empty_msds.sec2_ghs_pictograms == []
    assert empty_msds.sec2_hazard_statements == []

    assert ProductLabelExtraction().aphia_review_annotations == []
    empty_cert = NotarizedCertExtraction()
    assert empty_cert.cert_subtype is None
    assert empty_cert.issuing_state_or_country is None
    assert empty_cert.factory_state_or_country is None
    assert OfficialLetterExtraction().chinese_common_names == []


def test_populated_extraction_models_and_enum_validation() -> None:
    label = ProductLabelExtraction(
        ghs_pictograms=[
            {
                "verbatim_text": "Flame over circle diamond icon",
                "value": "GHS03",
                "page": 1,
                "box_2d": [100, 100, 200, 200],
            }
        ],
        aphia_review_annotations=[
            {
                "verbatim_text": "請加註蜜蜂劇毒警語",
                "value": "Add high bee-toxicity warning statement",
                "page": 1,
                "box_2d": [450, 50, 520, 400],
            }
        ],
    )
    assert label.ghs_pictograms[0].value == "GHS03"
    assert len(label.aphia_review_annotations) == 1

    cert = NotarizedCertExtraction(
        cert_subtype={"verbatim_text": "CERTIFICATE OF FREE SALE", "value": "free_sale", "page": 1, "box_2d": [50, 100, 90, 900]},
        issuing_state_or_country={"verbatim_text": "State of Delaware", "value": "Delaware, USA", "page": 1, "box_2d": [120, 100, 150, 400]},
        factory_state_or_country={"verbatim_text": "Houston, Texas", "value": "Texas, USA", "page": 1, "box_2d": [300, 100, 330, 500]},
        products=[
            {
                "brand_or_product_name": {"verbatim_text": "KUMULUS 80 DF", "value": "KUMULUS 80 DF", "page": 1, "box_2d": [350, 100, 380, 300]},
                "common_name": {"verbatim_text": "Sulfur", "value": "Sulfur", "page": 1, "box_2d": [350, 310, 380, 400]},
            }
        ],
    )
    assert cert.cert_subtype is not None and cert.cert_subtype.value == "free_sale"
    assert len(cert.products) == 1 and cert.products[0].common_name is not None
    assert not any(k.startswith("old_") for k in NotarizedCertExtraction.model_fields)

    wrapped = GroundedField[str](
        verbatim_text="Line 1\nLine 2",
        value="Line 1 Line 2",
        page=1,
        box_2d=[100, 400, 120, 900],
        extra_boxes_2d=[[122, 100, 142, 600]],
    )
    assert wrapped.extra_boxes_2d == [[122, 100, 142, 600]]

    msds = MsdsExtraction(
        issued_version={"verbatim_text": "Version 4.2 (2026-05-01)", "value": "4.2 / 2026-05-01", "page": 1, "box_2d": [20, 700, 50, 950]},
        sec2_ghs_pictograms=[{"verbatim_text": "Exclamation mark", "value": "GHS07", "page": 2, "box_2d": [200, 100, 280, 180]}],
    )
    assert msds.sec2_ghs_pictograms[0].value == "GHS07"

    letter = OfficialLetterExtraction(
        affected_reg_nos=[{"verbatim_text": "農藥進字0001", "value": "農藥進00001", "page": 1, "box_2d": [300, 120, 330, 260]}],
        chinese_common_names=[{"verbatim_text": "阿扶平", "value": "阿扶平", "page": 1, "box_2d": [300, 270, 330, 340]}],
        dosage_and_formulation=[{"verbatim_text": "100 g/L SL", "value": "100 g/L SL", "page": 1, "box_2d": [300, 350, 330, 460]}],
    )
    assert letter.affected_reg_nos[0].value == "農藥進00001"

    with pytest.raises(ValidationError):
        MsdsExtraction(
            sec2_ghs_pictograms=[{"verbatim_text": "Bad", "value": "GHS99", "page": 1, "box_2d": [10, 10, 50, 50]}]
        )
    with pytest.raises(ValidationError):
        NotarizedCertExtraction(
            cert_subtype={"verbatim_text": "Unknown", "value": "invalid_type", "page": 1, "box_2d": [10, 10, 50, 50]}  # type: ignore[arg-type]
        )


def test_google_genai_schema_compiler_preserves_descriptions_bounds_and_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    dummy_client = Client(api_key="test-key-not-called", vertexai=False)
    expected_order = ["verbatim_text", "value", "page", "box_2d", "extra_boxes_2d"]

    for doc_type, schema_cls in SCHEMA_BY_DOC_TYPE.items():
        compiled = _transformers.t_schema(dummy_client, schema_cls)
        assert compiled.properties, f"Compiled schema empty for {doc_type}"

    compiled_label = _transformers.t_schema(dummy_client, ProductLabelExtraction)
    reg_no_schema = compiled_label.properties["reg_no"]
    assert "農藥製00001" in (reg_no_schema.description or "")
    assert reg_no_schema.property_ordering == expected_order
    assert reg_no_schema.properties["box_2d"].items.minimum == 0
    assert reg_no_schema.properties["box_2d"].items.maximum == 1000

    ghs_item = compiled_label.properties["ghs_pictograms"].items
    assert ghs_item.property_ordering == expected_order
    assert ghs_item.properties["value"].enum == [f"GHS0{i}" for i in range(1, 10)]

    compiled_cert = _transformers.t_schema(dummy_client, NotarizedCertExtraction)
    subtype_schema = compiled_cert.properties["cert_subtype"]
    assert subtype_schema.property_ordering == expected_order
    assert subtype_schema.properties["value"].enum == ["loa", "moc", "free_sale", "loo", "other"]

    compiled_letter = _transformers.t_schema(dummy_client, OfficialLetterExtraction)
    assert "ROC date" in (compiled_letter.properties["issue_date"].description or "")
    assert compiled_letter.properties["mandate_clauses"].items.property_ordering == expected_order
