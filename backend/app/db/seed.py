# cSpell:words APHIA MSDSs
"""Idempotent seed fixtures for local development and compliance audit demos."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    PUBLIC_DEMO_COMPANY_ID,
    PUBLIC_DEMO_REG_NO,
    AphiaQueryCache,
    AphiaRecord,
    AuditReportCache,
    Company,
    Document,
    DocumentClause,
    MoaRegistry,
)
from app.schemas.report import ComplianceAuditReport, ComplianceChecklistItem
from app.schemas.extraction import (
    AuthorizedProductItem,
    MsdsExtraction,
    NotarizedCertExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.schemas.grounded import GroundedField
from app.services.aphia_client import _build_query_key
from app.services.parser import extract_clauses_for_db


def _gf(verbatim: str, value: str | None = None, page: int = 1) -> GroundedField[str]:
    return GroundedField[str](
        verbatim_text=verbatim,
        value=value if value is not None else verbatim,
        page=page,
        box_2d=[100, 100, 180, 500],
    )


async def seed_sample_data(session: AsyncSession) -> None:
    """Insert deterministic synthetic public_demo (農藥進99001) fixtures idempotently."""
    try:
        await session.execute(
            insert(Company)
            .values(company_id=PUBLIC_DEMO_COMPANY_ID, name="Public Demo")
            .on_conflict_do_nothing(index_elements=["company_id"])
        )

        moa_stmt = (
            insert(MoaRegistry)
            .values(
                reg_no=PUBLIC_DEMO_REG_NO,
                permit_type="農藥進",
                permit_number="99001",
                chinese_name="綠安寧",
                pesticide_code="F999",
                english_name="Verdantizole",
                brand_name="翠盾",
                company_name="範例農業科技股份有限公司",
                foreign_manufacturer="DemoChem Manufacturing GmbH",
                formulation="水懸劑",
                content_pct="25%",
                valid_until_roc="120/06/30",
                revocation_status=None,
                approved_usages_json=[
                    {
                        "作物名稱": "水稻",
                        "病蟲害名稱": "紋枯病",
                        "每公頃使用用藥量": "0.5 公升",
                        "稀釋倍數": "2000",
                        "使用時期": "發病初期開始施藥。",
                        "施藥間隔": "10",
                        "施用次數": "2",
                        "安全採收期": "14",
                        "注意事項": "1. 避免高溫時段噴施。2. 輪流使用不同作用機制藥劑。",
                    },
                    {
                        "作物名稱": "柑桔類",
                        "病蟲害名稱": "黑點病",
                        "每公頃使用用藥量": "0.6 公升",
                        "稀釋倍數": "1500",
                        "使用時期": "謝花後至幼果期施藥。",
                        "施藥間隔": "14",
                        "施用次數": "3",
                        "安全採收期": "21",
                        "注意事項": "1. 施藥時避免藥液飄散至鄰田。2. 本劑對蜜蜂相對無毒。",
                    },
                    {
                        "作物名稱": "茶",
                        "病蟲害名稱": "炭疽病",
                        "每公頃使用用藥量": "0.4 公升",
                        "稀釋倍數": "2500",
                        "使用時期": "發病初期施藥。",
                        "施藥間隔": "10",
                        "施用次數": "2",
                        "安全採收期": "21",
                        "注意事項": "採收前21日停止施藥。",
                    },
                ],
                raw_core_json={
                    "許可證字": "農藥進",
                    "許可證號": "99001",
                    "中文名稱": "綠安寧",
                    "英文名稱": "Verdantizole",
                    "廠牌名稱": "翠盾",
                    "廠商名稱": "範例農業科技股份有限公司",
                    "國外原製造廠商": "DemoChem Manufacturing GmbH",
                    "劑型": "水懸劑",
                    "含量": "25%",
                    "有效期限": "120/06/30",
                    "synthetic_demo": True,
                },
            )
            .on_conflict_do_nothing(index_elements=["reg_no"])
        )
        await session.execute(moa_stmt)

        existing_baseline = (
            await session.execute(
                select(Document.doc_id).where(
                    Document.company_id == PUBLIC_DEMO_COMPANY_ID,
                    Document.reg_no == PUBLIC_DEMO_REG_NO,
                    Document.doc_type == "label",
                    Document.is_active_baseline.is_(True),
                )
            )
        ).scalar_one_or_none()

        label_model = ProductLabelExtraction(
            reg_no=_gf(PUBLIC_DEMO_REG_NO),
            common_name=_gf("綠安寧"),
            formulation_type=_gf("水懸劑"),
            dosage_and_content=_gf("25%"),
            foreign_manufacturer_name=_gf("DemoChem Manufacturing GmbH"),
            local_permit_holder_name=_gf("範例農業科技股份有限公司"),
            ghs_pictograms=[_gf("GHS07", "GHS07")],
            hazard_statements=[_gf("造成嚴重眼睛刺激（H319）", "H319"), _gf("吞食可能有害（H303）", "H303")],
        )
        msds_model = MsdsExtraction(
            common_name=_gf("Verdantizole"),
            formulation_type=_gf("SC"),
            dosage_and_content=_gf("25%"),
            issued_version=_gf("Version 5.0", "5.0"),
            sec2_ghs_pictograms=[_gf("GHS07", "GHS07"), _gf("GHS09", "GHS09")],
            sec2_hazard_statements=[_gf("H319: Causes serious eye irritation.", "H319")],
        )
        cert_model = NotarizedCertExtraction(
            cert_subtype=_gf("Letter of Authorization", "loa"),
            products=[AuthorizedProductItem(common_name=_gf("Verdantizole"))],
            certified_factory_name=_gf("Helios AgroScience S.A."),
        )
        letter_model = OfficialLetterExtraction(
            issue_number=_gf("防檢二字第1159900123號"),
            affected_reg_nos=[_gf("農藥進字第99001號", PUBLIC_DEMO_REG_NO)],
            chinese_common_names=[_gf("綠安寧")],
            mandate_clauses=[_gf("本劑對水生生物具高毒性，請勿使用於水源保護區及魚塭周邊")],
        )

        docs = [
            ("seed_label_99001", "01_label_99001.pdf", "label", PUBLIC_DEMO_REG_NO, "綠安寧", existing_baseline is None, 3, label_model),
            ("seed_msds_99001_v5", "02_msds_99001_v5.pdf", "msds", PUBLIC_DEMO_REG_NO, "Verdantizole", False, 4, msds_model),
            ("seed_cert_99001", "03_change_cert_99001.pdf", "change_cert", PUBLIC_DEMO_REG_NO, "Verdantizole", False, 3, cert_model),
            ("seed_letter_99001", "04_official_letter_99001.pdf", "official_letter", PUBLIC_DEMO_REG_NO, "綠安寧", False, 1, letter_model),
        ]

        for doc_id, filename, doc_type, reg_no, common_name, is_baseline, page_count, model in docs:
            res = await session.execute(
                insert(Document)
                .values(
                    doc_id=doc_id,
                    company_id=PUBLIC_DEMO_COMPANY_ID,
                    filename=filename,
                    doc_type=doc_type,
                    reg_no=reg_no,
                    common_name=common_name,
                    is_active_baseline=is_baseline,
                    page_count=page_count,
                    extracted_data=model.model_dump(),
                )
                .on_conflict_do_nothing(index_elements=["doc_id"])
                .returning(Document.doc_id)
            )
            if res.scalar_one_or_none() is not None:
                for clause_dict in extract_clauses_for_db(
                    doc_id=doc_id,
                    doc_type=doc_type,
                    reg_no=reg_no,
                    common_name=common_name,
                    extraction=model,
                ):
                    session.add(DocumentClause(**clause_dict))

        demo_report = ComplianceAuditReport(
            reg_no=PUBLIC_DEMO_REG_NO,
            product_name_zh="綠安寧",
            product_name_en="Verdantizole",
            overall_status="pending_moa_approval",
            executive_summary_zh="此為公開展示範例（農藥進99001 綠安寧）：變更證明文件之原製造廠與現行標示及農業部登記不一致，建議取得變更核准後再更新標籤。",
            executive_summary_en="Public demo fixture (農藥進99001 Verdantizole): factory change certificate differs from current label and MOA registry; update label after MOA approval.",
            checklist_items=[
                ComplianceChecklistItem(
                    field_name="國外原製造廠名稱",
                    category="factory_source_change",
                    action_status="pending_moa_approval",
                    current_label_value="DemoChem Manufacturing GmbH",
                    recommended_new_value="Helios AgroScience S.A.",
                    rationale="Seeded synthetic demo diff between baseline label and uploaded change certificate.",
                )
            ],
            msds_doc_id_used="seed_msds_99001_v5",
        )
        await session.execute(
            insert(AuditReportCache)
            .values(
                company_id=PUBLIC_DEMO_COMPANY_ID,
                reg_no=PUBLIC_DEMO_REG_NO,
                cache_key="seed_demo_99001",
                report_json=demo_report.model_dump(),
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )

        demo_carcinogen = {
            "source_kind": "carcinogen",
            "source_key": "demo_carcinogen_99001",
            "title": "綠安寧 (Verdantizole) — 公開示範可能致癌評估狀態（合成範例）",
            "common_name": "綠安寧",
            "published_date": "2025-10-15",
            "detail_url": "https://pesticide.aphia.gov.tw/information/Data/Opendata/3",
        }
        demo_assessment = {
            "source_kind": "assessment",
            "source_key": "demo_assessment_99001",
            "title": "綠安寧（Verdantizole）十五年安全性評估資料（公開示範範例）",
            "common_name": "綠安寧",
            "published_date": "2026-01-20",
            "detail_url": "https://pesticide.aphia.gov.tw/information/Data/Opendata/2",
        }
        demo_pre_notice = {
            "source_kind": "announcement",
            "source_key": "demo_prenotice_99001",
            "title": "預先通知公告：綠安寧標示加註水生生物警語（公開示範範例）",
            "common_name": "綠安寧",
            "published_date": "2026-03-01",
            "detail_url": "https://pesticide.aphia.gov.tw/information/Data/News",
        }
        await session.execute(
            insert(AphiaRecord)
            .values(**demo_carcinogen, raw_fields_json={"synthetic_demo": True})
            .on_conflict_do_nothing(constraint="uq_aphia_source")
        )
        demo_tokens = ["綠安寧", "Verdantizole"]
        await session.execute(
            insert(AphiaQueryCache)
            .values(
                query_key=_build_query_key(demo_tokens),
                keywords_json=demo_tokens,
                result_json={
                    "synthetic_demo": True,
                    "carcinogen_matches": [demo_carcinogen],
                    "assessment_matches": [demo_assessment],
                    "latest_pre_notice": demo_pre_notice,
                },
            )
            .on_conflict_do_nothing(index_elements=["query_key"])
        )

        await session.commit()
    except Exception:
        await session.rollback()
        raise


if __name__ == "__main__":
    import asyncio
    from app.config import Settings
    from app.db.session import build_async_engine, build_session_factory, init_db_schema

    async def _run() -> None:
        engine = build_async_engine(Settings())
        await init_db_schema(engine)
        async with build_session_factory(engine)() as s:
            await seed_sample_data(s)
        await engine.dispose()

    asyncio.run(_run())
