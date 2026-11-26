# cSpell:words APHIA BASF Metaflumizone Brasil Guaratingueta Paulo
"""Unit tests for CL 4.1: deterministic cascade diff modeled on real BASF & Taiwan RA cases."""

from __future__ import annotations

import pytest

from app.db.models import MoaRegistry
from app.schemas.extraction import (
    AuthorizedProductItem,
    MsdsExtraction,
    NotarizedCertExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.services.cascade_diff import normalize_reg_no, run_cascade_diff


def _gf(text: str, value: str | None = None, page: int = 1, box: list[int] | None = None) -> dict:
    return {
        "verbatim_text": text,
        "value": text if value is None else value,
        "page": page,
        "box_2d": box or [100, 100, 150, 500],
    }


def test_normalize_reg_no_handles_fullwidth_official_letter_and_label_formats() -> None:
    assert normalize_reg_no("農藥進字0001") == "農藥進00001"
    assert normalize_reg_no("農藥進字第０００１號") == "農藥進00001"
    assert normalize_reg_no("農藥許可證農藥進字第03225號") == "農藥進03225"
    assert normalize_reg_no("農藥原進00001") == "農藥原進00001"
    assert normalize_reg_no("農藥原製字第195號") == "農藥原製00195"


def test_cascade_diff_catches_all_dimensions_with_exact_citations() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥許可證農藥進字第03225號", "農藥進03225"),
        common_name=_gf("美氟綜"),
        foreign_manufacturer_name=_gf("Old Factory Ltd.", box=[200, 100, 230, 400]),
        foreign_manufacturer_address=_gf("Old Street 1, Brazil", box=[235, 100, 260, 450]),
        local_permit_holder_name=_gf("台灣巴斯夫股份有限公司"),
        ghs_pictograms=[
            _gf("GHS08", "GHS08", box=[500, 100, 560, 160]),
            _gf("GHS09", "GHS09", box=[500, 170, 560, 230]),
        ],
        hazard_statements=[_gf("H320造成眼睛刺激", "H320")],
        aphia_review_annotations=[_gf("請加註蜜蜂警語", "Add bee warning", box=[700, 50, 740, 350])],
    )

    cert = NotarizedCertExtraction(
        cert_subtype=_gf("LETTER OF AUTHORIZATION", "loa"),
        issuing_state_or_country=_gf("Limburgerhof, Germany", "Germany"),
        factory_state_or_country=_gf("Sao Paulo, Brazil", "Brazil"),
        authorized_entity_name=_gf("台灣巴斯夫股份有限公司"),
        certified_factory_name=_gf("BASF Agricultural Solutions Brasil Ltda.", page=2, box=[300, 100, 330, 600]),
        certified_factory_address=_gf("Avenida Brasil, 791 Guaratingueta, 12521-000 Sao Paulo, Brazil", page=2),
        products=[AuthorizedProductItem(common_name=_gf("Metaflumizone", page=2))],
    )

    msds = MsdsExtraction(
        common_name=_gf("Metaflumizone"),
        issued_version=_gf("Version: 4.0", "4.0"),
        sec2_ghs_pictograms=[
            _gf("GHS07", "GHS07", page=2, box=[174, 165, 217, 217]),
            _gf("GHS09", "GHS09", page=2, box=[174, 233, 217, 285]),
        ],
        sec2_hazard_statements=[
            _gf("H320: Causes eye irritation.", "H320", page=2),
            _gf("H361fd: Suspected of damaging fertility.", "H361fd", page=2),
        ],
    )

    moa = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        company_name="台灣巴斯夫股份有限公司",
        foreign_manufacturer="Old Factory Ltd.",
        raw_core_json={},
    )

    letter_match = OfficialLetterExtraction(
        issue_number=_gf("防檢二字第1150001234號"),
        compliance_deadline=_gf("中華民國115年12月31日", "2026-12-31"),
        affected_reg_nos=[_gf("農藥進字03225", "農藥進字03225")],  # unnormalized -> still matches
        chinese_common_names=[_gf("美氟綜")],
        mandate_clauses=[_gf("請於標籤加註水生生物緩衝帶警語", "Add aquatic buffer zone warning")],
    )
    # Different permit sharing same common_name must NOT bleed into 農藥進03225
    letter_other_permit = OfficialLetterExtraction(
        issue_number=_gf("防檢二字第1150009999號"),
        affected_reg_nos=[_gf("農藥進字09999", "農藥進09999")],
        chinese_common_names=[_gf("美氟綜")],
        mandate_clauses=[_gf("Other permit only")],
    )

    findings = run_cascade_diff(
        reg_no="農藥進03225",
        label=label,
        label_doc_id="label_03225",
        msds=msds,
        msds_doc_id="msds_alverde_v4",
        change_cert=cert,
        cert_doc_id="loa_2026",
        moa=moa,
        official_letters=[("letter_115", letter_match), ("letter_other", letter_other_permit)],
    )

    factory_name_f = next(f for f in findings if f.field_name == "國外原製造廠名稱")
    assert factory_name_f.action_status == "pending_moa_approval"
    assert factory_name_f.current_label_citation is not None
    assert factory_name_f.current_label_citation.doc_id == "label_03225"
    assert factory_name_f.supporting_citations[0].doc_id == "loa_2026"
    assert factory_name_f.supporting_citations[0].page == 2

    # Address must also be pending_moa_approval while name is pending approval
    factory_addr_f = next(f for f in findings if f.field_name == "國外原製造廠地址")
    assert factory_addr_f.action_status == "pending_moa_approval"

    assert any(f.category == "moa_registry_sync" and f.action_status == "pending_moa_approval" for f in findings)
    assert any(f.field_name == "證明文件簽發地與製造廠所在地差異" and f.action_status == "verify_warning" for f in findings)
    assert any(f.field_name == "GHS 危害圖式 (GHS01–GHS09)" and f.action_status == "must_update" for f in findings)

    h_f = next(f for f in findings if f.field_name == "危害警告訊息 (GHS Hazard Statements)")
    assert h_f.action_status == "must_update"
    assert "H361FD" in h_f.recommended_new_value.upper()

    aphia_f = next(f for f in findings if f.category == "aphia_label_instruction")
    assert aphia_f.current_label_citation is not None and aphia_f.supporting_citations == []

    letter_findings = [f for f in findings if f.category == "official_letter_mandate"]
    assert len(letter_findings) == 1
    assert "2026-12-31" in letter_findings[0].recommended_new_value


def test_ghs_chinese_only_label_and_empty_label_statuses() -> None:
    msds = MsdsExtraction(
        sec2_hazard_statements=[_gf("H301+H311+H331", "H301+H311+H331")]
    )
    # Traditional Chinese label without H-codes -> verify_warning
    cn_label = ProductLabelExtraction(hazard_statements=[_gf("吞食中毒、皮膚接觸中毒")])
    f_cn = run_cascade_diff(
        reg_no="農藥製00001", label=cn_label, label_doc_id="l1", msds=msds, msds_doc_id="m1"
    )
    assert f_cn[0].action_status == "verify_warning"

    # Empty label hazard_statements -> must_update (not downgraded to verify_warning)
    empty_label = ProductLabelExtraction(hazard_statements=[])
    f_empty = run_cascade_diff(
        reg_no="農藥製00001", label=empty_label, label_doc_id="l1", msds=msds, msds_doc_id="m1"
    )
    assert f_empty[0].action_status == "must_update"


def test_common_name_and_loa_do_not_conflate_authorizer_with_factory() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進03225"),
        common_name=_gf("錯字普通名"),
        foreign_manufacturer_name=_gf("Real Factory"),
    )
    # LoA without certified_factory_name must NOT treat authorizing_entity_name (HQ) as factory
    cert = NotarizedCertExtraction(
        cert_subtype=_gf("LoA", "loa"),
        authorizing_entity_name=_gf("German HQ GmbH"),
        products=[AuthorizedProductItem(common_name=_gf("WrongActive"))],
    )
    moa = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        foreign_manufacturer="Real Factory",
        raw_core_json={},
    )
    findings = run_cascade_diff(
        reg_no="農藥進03225",
        label=label,
        label_doc_id="l1",
        change_cert=cert,
        cert_doc_id="c1",
        moa=moa,
    )
    assert not any(f.category in ("factory_source_change", "moa_registry_sync") for f in findings)
    assert sum(1 for f in findings if f.category == "common_name_mismatch") == 2


def test_validation_rejects_missing_doc_ids_and_mismatched_reg_no() -> None:
    label = ProductLabelExtraction(reg_no=_gf("農藥製00001"))
    with pytest.raises(ValueError, match="label_doc_id"):
        run_cascade_diff(reg_no="農藥製00001", label=label)
    with pytest.raises(ValueError, match="does not match target"):
        run_cascade_diff(reg_no="農藥製99999", label=label, label_doc_id="l1")


def test_cascade_diff_compliant_when_all_sources_match() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進03225"),
        common_name=_gf("美氟綜"),
        foreign_manufacturer_name=_gf("BASF Agricultural Solutions Brasil Ltda."),
        foreign_manufacturer_address=_gf("Avenida Brasil, 791, Brazil"),
        local_permit_holder_name=_gf("台灣巴斯夫股份有限公司"),
        ghs_pictograms=[_gf("GHS08", "GHS08"), _gf("GHS09", "GHS09")],
        hazard_statements=[_gf("H320"), _gf("H361")],
    )
    cert = NotarizedCertExtraction(
        issuing_state_or_country=_gf("Brazil"),
        factory_state_or_country=_gf("Brazil"),
        authorized_entity_name=_gf("台灣巴斯夫股份有限公司"),
        certified_factory_name=_gf("BASF Agricultural Solutions Brasil Ltda."),
        certified_factory_address=_gf("Avenida Brasil, 791, Brazil"),
        products=[AuthorizedProductItem(common_name=_gf("Metaflumizone"))],
    )
    msds = MsdsExtraction(
        sec2_ghs_pictograms=[_gf("GHS08", "GHS08"), _gf("GHS09", "GHS09")],
        sec2_hazard_statements=[_gf("H320"), _gf("H361")],
    )
    moa = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        company_name="台灣巴斯夫股份有限公司",
        foreign_manufacturer="BASF Agricultural Solutions Brasil Ltda.",
        raw_core_json={},
    )

    assert (
        run_cascade_diff(
            reg_no="農藥進03225",
            label=label,
            label_doc_id="l1",
            msds=msds,
            msds_doc_id="m1",
            change_cert=cert,
            cert_doc_id="c1",
            moa=moa,
        )
        == []
    )


def test_technical_grade_permit_no_false_positives_and_letter_isolation() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥原進00001"),
        common_name=_gf("美氟綜"),
        formulation_type=None,
        dosage_and_content=_gf("95%"),
    )
    cert = NotarizedCertExtraction(
        products=[
            AuthorizedProductItem(
                common_name=_gf("Metaflumizone Technical"),
                formulation_type=_gf("Technical"),
                dosage_and_content=_gf("95%"),
            )
        ]
    )
    msds = MsdsExtraction(
        common_name=_gf("METAFLUMIZONE"),
        formulation_type=_gf("原體"),
        dosage_and_content=_gf("95%"),
    )
    moa = MoaRegistry(
        reg_no="農藥原進00001",
        permit_type="農藥原進",
        permit_number="00001",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        formulation="原體",
        content_pct="95%",
        raw_core_json={},
    )
    formulated_letter = OfficialLetterExtraction(
        issue_number=_gf("防檢二字第1140001號"),
        chinese_common_names=[_gf("美氟綜")],
        dosage_and_formulation=[_gf("100 g/L SL")],
        mandate_clauses=[_gf("加註警語")],
    )
    findings = run_cascade_diff(
        reg_no="農藥原進00001",
        label=label,
        label_doc_id="l1",
        msds=msds,
        msds_doc_id="m1",
        change_cert=cert,
        cert_doc_id="c1",
        moa=moa,
        official_letters=[("let1", formulated_letter)],
    )
    assert findings == []


def test_msds_linkage_obsolete_hazard_and_shorthand_hcodes() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進03225"),
        common_name=_gf("美氟綜"),
        formulation_type=_gf("SC"),
        ghs_pictograms=[_gf("GHS07", "GHS07")],
        hazard_statements=[_gf("H301+311+331")],
    )
    # Wrong active ingredient & formulation, and empty sec2_hazard_statements
    msds = MsdsExtraction(
        common_name=_gf("Metalaxyl"),
        formulation_type=_gf("EC"),
        issued_version=_gf("v2.0"),
        sec2_ghs_pictograms=[_gf("GHS07", "GHS07"), _gf("GHS09", "GHS09")],
        sec2_hazard_statements=[],
    )
    moa = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METALAXYL-M",
        formulation="SC",
        raw_core_json={},
    )
    findings = run_cascade_diff(
        reg_no="農藥進字第003225號",
        label=label,
        label_doc_id="l1",
        msds=msds,
        msds_doc_id="m1",
        moa=moa,
    )
    by_field = {f.field_name: f for f in findings}
    assert by_field["MSDS 連結驗證 — 有效成分普通名稱"].action_status == "verify_warning"
    assert by_field["MSDS 連結驗證 — 劑型 (Formulation)"].action_status == "verify_warning"
    assert by_field["GHS 危害圖式 (GHS01–GHS09)"].current_label_citation is not None
    obsolete = by_field["危害警告訊息 (GHS Hazard Statements)"]
    assert obsolete.action_status == "must_update"
    assert obsolete.recommended_new_value == "(無 / None)"
    assert obsolete.current_label_citation is not None


def test_label_none_local_entity_and_validation_edges() -> None:
    assert normalize_reg_no("農藥進字第003225號") == "農藥進03225"

    msds = MsdsExtraction(issued_version=_gf("2025-01-01"))
    cert = NotarizedCertExtraction(authorized_entity_name=_gf("New Agent Co."))
    moa = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        company_name="Old Agent Co.",
        raw_core_json={},
    )
    findings = run_cascade_diff(
        reg_no="農藥進03225",
        msds=msds,
        msds_doc_id="m1",
        change_cert=cert,
        cert_doc_id="c1",
        moa=moa,
    )
    cats = {f.category: f for f in findings}
    assert cats["ghs_hazard_drift"].action_status == "verify_warning"
    assert cats["moa_registry_sync"].action_status == "pending_moa_approval"

    with pytest.raises(ValueError, match="Invalid Taiwan pesticide registration number"):
        run_cascade_diff(reg_no="invalid-reg")
    with pytest.raises(ValueError, match="msds_doc_id"):
        run_cascade_diff(reg_no="農藥進03225", msds=msds)
    with pytest.raises(ValueError, match="cert_doc_id"):
        run_cascade_diff(reg_no="農藥進03225", change_cert=cert)


def test_multi_product_loa_matches_target_mixture_and_flags_unmatched_loa() -> None:
    multi_loa = NotarizedCertExtraction(
        cert_subtype=_gf("Letter of Authorization", "loa"),
        certified_factory_name=_gf("BASF Agricultural Solutions Deutschland GmbH"),
        products=[
            AuthorizedProductItem(
                brand_or_product_name=_gf("KUMULUS 80 DF"),
                product_internal_code=_gf("BAS 175 01 F"),
                common_name=_gf("Sulfur"),
                dosage_and_content=_gf("80%"),
                formulation_type=_gf("WG"),
            ),
            AuthorizedProductItem(
                brand_or_product_name=_gf("CABRIO TOP"),
                product_internal_code=_gf("BAS 518 01 F"),
                common_name=_gf("Metiram + Pyraclostrobin"),
                dosage_and_content=_gf("55% + 5%"),
                formulation_type=_gf("WG"),
            ),
        ],
    )
    moa_cabrio = MoaRegistry(
        reg_no="農藥進02222",
        permit_type="農藥進",
        permit_number="02222",
        chinese_name="百克敏+免得爛",
        english_name="METIRAM + PYRACLOSTROBIN",
        brand_name="CABRIO TOP",
        formulation="WG",
        content_pct="55% + 5%",
        foreign_manufacturer="BASF Agricultural Solutions Deutschland GmbH",
        raw_core_json={},
    )
    assert (
        run_cascade_diff(
            reg_no="農藥進02222",
            change_cert=multi_loa,
            cert_doc_id="loa_multi",
            moa=moa_cabrio,
        )
        == []
    )

    moa_other = MoaRegistry(
        reg_no="農藥進09999",
        permit_type="農藥進",
        permit_number="09999",
        chinese_name="其他",
        english_name="AZOXYSTROBIN",
        foreign_manufacturer="BASF Agricultural Solutions Deutschland GmbH",
        raw_core_json={},
    )
    unmatched = run_cascade_diff(
        reg_no="農藥進09999",
        change_cert=multi_loa,
        cert_doc_id="loa_multi",
        moa=moa_other,
    )
    assert len(unmatched) == 1
    assert unmatched[0].category == "common_name_mismatch"
    assert unmatched[0].action_status == "verify_warning"

    # 1. Formulated permit with missing formulation_type must NOT be misclassified as 原體
    msds_sc = MsdsExtraction(common_name=_gf("Azoxystrobin"), formulation_type=_gf("SC"))
    lbl_no_form = ProductLabelExtraction(
        reg_no=_gf("農藥進09999"),
        common_name=_gf("其他"),
        foreign_manufacturer_name=_gf("BASF Agricultural Solutions Deutschland GmbH"),
    )
    assert run_cascade_diff(reg_no="農藥進09999", label=lbl_no_form, label_doc_id="l1", msds=msds_sc, msds_doc_id="m1", moa=moa_other) == []

    # 2. Wrong active ingredient with matching formulation+dosage must NOT match
    wrong_ai_same_form = NotarizedCertExtraction(
        products=[AuthorizedProductItem(common_name=_gf("Mancozeb"), dosage_and_content=_gf("80%"), formulation_type=_gf("WG"))]
    )
    moa_sulfur = MoaRegistry(
        reg_no="農藥進00569", permit_type="農藥進", permit_number="00569",
        chinese_name="硫黃", english_name="SULFUR", formulation="WG", content_pct="80%", raw_core_json={},
    )
    res_wrong_ai = run_cascade_diff(reg_no="農藥進00569", change_cert=wrong_ai_same_form, cert_doc_id="c1", moa=moa_sulfur)
    assert len(res_wrong_ai) == 1 and res_wrong_ai[0].category == "common_name_mismatch"

    # 3. Single-ingredient row must NOT match mixture permit ("METIRAM + PYRACLOSTROBIN")
    single_ai_loa = NotarizedCertExtraction(products=[AuthorizedProductItem(common_name=_gf("Pyraclostrobin"))])
    res_single = run_cascade_diff(reg_no="農藥進02222", change_cert=single_ai_loa, cert_doc_id="c1", moa=moa_cabrio)
    assert len(res_single) == 1 and res_single[0].category == "common_name_mismatch"


def test_loo_ignored_moc_vs_loa_cross_check_and_other_cert_warning() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進00569"),
        common_name=_gf("硫黃"),
        foreign_manufacturer_name=_gf("Factory A"),
    )
    moa = MoaRegistry(
        reg_no="農藥進00569",
        permit_type="農藥進",
        permit_number="00569",
        chinese_name="硫黃",
        english_name="SULFUR",
        foreign_manufacturer="Factory A",
        raw_core_json={},
    )
    # LoO states active-ingredient origin factory (NOT on label) -> must NOT trigger factory findings
    loo = NotarizedCertExtraction(
        cert_subtype=_gf("Letter of Origin", "loo"),
        certified_factory_name=_gf("Active Ingredient Origin Co."),
        products=[AuthorizedProductItem(common_name=_gf("Sulfur"))],
    )
    assert run_cascade_diff(reg_no="農藥進00569", label=label, label_doc_id="l1", change_cert=loo, cert_doc_id="loo1", moa=moa) == []

    # LoA + MoC stating different factories -> verify_warning cross-consistency finding
    loa = NotarizedCertExtraction(
        cert_subtype=_gf("LoA", "loa"),
        certified_factory_name=_gf("Factory A"),
        products=[AuthorizedProductItem(common_name=_gf("Sulfur"))],
    )
    moc = NotarizedCertExtraction(
        cert_subtype=_gf("MoC", "moc"),
        certified_factory_name=_gf("Factory B"),
        products=[AuthorizedProductItem(common_name=_gf("Sulfur"))],
    )
    other = NotarizedCertExtraction(
        cert_subtype=_gf("Other", "other"),
        certificate_title=_gf("Special Addendum"),
    )
    res = run_cascade_diff(
        reg_no="農藥進00569",
        label=label,
        label_doc_id="l1",
        change_cert=loa,
        cert_doc_id="loa1",
        extra_certs=[("moc1", moc), ("oth1", other)],
        moa=moa,
    )
    fields = {f.field_name: f for f in res}
    assert fields["證明文件製造廠名稱一致性 (LoA vs MoC)"].action_status == "verify_warning"
    assert fields["其他證明文件人工確認 (Special Addendum)"].action_status == "verify_warning"


def test_english_label_common_name_zh_formulation_moa_dosage_and_str_addr_match() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進00569"),
        common_name=_gf("Sulfur"),
        formulation_type=_gf("水分散性粒劑"),
        dosage_and_content=_gf("80%"),
        foreign_manufacturer_name=_gf("BASF Agricultural Solutions Deutschland GmbH"),
        foreign_manufacturer_address=_gf("Carl-Bosch-Strasse 38, 67056 Ludwigshafen, Germany"),
    )
    moc = NotarizedCertExtraction(
        cert_subtype=_gf("MoC", "moc"),
        certified_factory_name=_gf("BASF Agricultural Solutions Deutschland GmbH"),
        certified_factory_address=_gf("Carl-Bosch-Str. 38, 67056 Ludwigshafen, Germany"),
        products=[
            AuthorizedProductItem(
                common_name=_gf("Sulfur"),
                dosage_and_content=_gf("80 %"),
                formulation_type=_gf("WG"),
            )
        ],
    )
    moa = MoaRegistry(
        reg_no="農藥進00569",
        permit_type="農藥進",
        permit_number="00569",
        chinese_name="可濕性硫黃",
        english_name="SULFUR",
        formulation="WG",
        content_pct="80.000 (%)",
        foreign_manufacturer="BASF Agricultural Solutions Deutschland GmbH",
        raw_core_json={},
    )
    assert run_cascade_diff(reg_no="農藥進00569", label=label, label_doc_id="l1", change_cert=moc, cert_doc_id="moc1", moa=moa) == []


def test_bilingual_msds_iupac_cas_canton_and_parenthetical_entity_no_false_positives() -> None:
    label = ProductLabelExtraction(
        reg_no=_gf("農藥進99001"),
        common_name=_gf("綠安寧"),
        iupac_name=_gf("methyl 2-[2-(4-chlorophenyl)ethoxy]-N-(2-methoxyethyl)acetamide"),
        formulation_type=_gf("水懸劑"),
        dosage_and_content=_gf("25%"),
        foreign_manufacturer_name=_gf("Helios AgroScience S.A."),
        foreign_manufacturer_address=_gf("Grenzacherstrasse 124, 4058 Basel, Switzerland"),
        local_permit_holder_name=_gf("範例農業科技股份有限公司"),
    )
    msds = MsdsExtraction(
        common_name=_gf("綠安寧 / Verdantizole"),
        iupac_or_cas_name=_gf("methyl 2-[2-(4-chlorophenyl)ethoxy]-N-(2-methoxyethyl)acetamide, CAS 99001-25-0"),
        formulation_type=_gf("SC"),
        dosage_and_content=_gf("25%"),
    )
    loa = NotarizedCertExtraction(
        cert_subtype=_gf("LoA", "loa"),
        issuing_state_or_country=_gf("Basel-Stadt, Switzerland"),
        factory_state_or_country=_gf("Basel, Switzerland"),
        authorized_entity_name=_gf("範例農業科技股份有限公司 (Demo AgroTech Co., Ltd.)"),
        certified_factory_name=_gf("Helios AgroScience S.A."),
        certified_factory_address=_gf("Grenzacherstrasse 124, 4058 Basel, Switzerland"),
        products=[AuthorizedProductItem(common_name=_gf("Verdantizole"))],
    )
    moa = MoaRegistry(
        reg_no="農藥進99001",
        permit_type="農藥進",
        permit_number="99001",
        chinese_name="綠安寧",
        english_name="Verdantizole",
        company_name="範例農業科技股份有限公司",
        foreign_manufacturer="Helios AgroScience S.A.",
        formulation="水懸劑",
        content_pct="25%",
        raw_core_json={},
    )
    assert (
        run_cascade_diff(
            reg_no="農藥進99001",
            label=label,
            label_doc_id="l1",
            msds=msds,
            msds_doc_id="m1",
            change_cert=loa,
            cert_doc_id="c1",
            moa=moa,
        )
        == []
    )

    # Guardrail: same-script parenthetical (e.g. "(Old Plant)") and different states still flag
    diff_cert = NotarizedCertExtraction(
        cert_subtype=_gf("LoA", "loa"),
        issuing_state_or_country=_gf("Texas, USA"),
        factory_state_or_country=_gf("California, USA"),
        authorized_entity_name=_gf("Demo Corp. (Old Branch)"),
        certified_factory_name=_gf("Helios AgroScience S.A."),
        products=[AuthorizedProductItem(common_name=_gf("Verdantizole"))],
    )
    moa_us = MoaRegistry(
        reg_no="農藥進99001",
        permit_type="農藥進",
        permit_number="99001",
        chinese_name="綠安寧",
        english_name="Verdantizole",
        company_name="Demo Corp.",
        foreign_manufacturer="Helios AgroScience S.A.",
        raw_core_json={},
    )
    flagged = run_cascade_diff(reg_no="農藥進99001", change_cert=diff_cert, cert_doc_id="c2", moa=moa_us)
    fields = {f.field_name for f in flagged}
    assert "證明文件簽發地與製造廠所在地差異" in fields
    assert "MOA 登記國內業者同步狀態" in fields
