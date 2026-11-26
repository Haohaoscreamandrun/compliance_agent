# cSpell:words APHIA MSDSs
"""Idempotent seed fixtures for local development and compliance audit demos."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    PUBLIC_DEMO_COMPANY_ID,
    PUBLIC_DEMO_REG_NO,
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
                chinese_name="示範殺菌劑",
                pesticide_code="F999",
                english_name="DEMOFUNGIN",
                brand_name="DemoShield",
                company_name="示範農化股份有限公司",
                foreign_manufacturer="Demo AgroChem Corp. (Old Plant)",
                formulation="SC",
                content_pct="22%",
                valid_until_roc="120/06/30",
                revocation_status=None,
                raw_core_json={
                    "許可證字": "農藥進",
                    "許可證號": "99001",
                    "中文名稱": "示範殺菌劑",
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
            common_name=_gf("示範殺菌劑"),
            formulation_type=_gf("SC"),
            dosage_and_content=_gf("22%"),
            foreign_manufacturer_name=_gf("Demo AgroChem Corp. (Old Plant)"),
            local_permit_holder_name=_gf("示範農化股份有限公司"),
            ghs_pictograms=[_gf("GHS07", "GHS07")],
            hazard_statements=[_gf("H320造成眼睛刺激", "H320")],
        )
        msds_model = MsdsExtraction(
            common_name=_gf("Demofungin"),
            formulation_type=_gf("SC"),
            dosage_and_content=_gf("22%"),
            issued_version=_gf("Version 4.0", "4.0"),
            sec2_ghs_pictograms=[_gf("GHS07", "GHS07"), _gf("GHS09", "GHS09")],
            sec2_hazard_statements=[_gf("H320: Causes eye irritation.", "H320")],
        )
        cert_model = NotarizedCertExtraction(
            cert_subtype=_gf("Free Sale", "free_sale"),
            products=[AuthorizedProductItem(common_name=_gf("Demofungin"))],
            certified_factory_name=_gf("Demo AgroChem GmbH (New Plant)"),
        )
        letter_model = OfficialLetterExtraction(
            issue_number=_gf("防檢示範字第1150009901號"),
            affected_reg_nos=[_gf("農藥進字第99001號", PUBLIC_DEMO_REG_NO)],
            chinese_common_names=[_gf("示範殺菌劑")],
            mandate_clauses=[_gf("請於標籤加註水生生物警語")],
        )

        docs = [
            ("seed_label_99001", "demo_label_99001.webp", "label", PUBLIC_DEMO_REG_NO, "示範殺菌劑", existing_baseline is None, 1, label_model),
            ("seed_msds_99001_v4", "demo_msds_99001_v4.pdf", "msds", PUBLIC_DEMO_REG_NO, "Demofungin", False, 12, msds_model),
            ("seed_cert_99001", "demo_cor_99001.pdf", "change_cert", PUBLIC_DEMO_REG_NO, "Demofungin", False, 2, cert_model),
            ("seed_letter_99001", "demo_aphia_letter_99001.pdf", "official_letter", PUBLIC_DEMO_REG_NO, "示範殺菌劑", False, 1, letter_model),
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
            product_name_zh="示範殺菌劑",
            product_name_en="DEMOFUNGIN",
            overall_status="pending_moa_approval",
            executive_summary_zh="此為公開展示範例（農藥進99001 示範殺菌劑）：變更證明文件之原製造廠與現行標示及農業部登記不一致，建議取得變更核准後再更新標籤。",
            executive_summary_en="Public demo fixture (農藥進99001 DEMOFUNGIN): factory change certificate differs from current label and MOA registry; update label after MOA approval.",
            checklist_items=[
                ComplianceChecklistItem(
                    field_name="國外原製造廠名稱",
                    category="factory_source_change",
                    action_status="pending_moa_approval",
                    current_label_value="Demo AgroChem Corp. (Old Plant)",
                    recommended_new_value="Demo AgroChem GmbH (New Plant)",
                    rationale="Seeded synthetic demo diff between baseline label and uploaded change certificate.",
                )
            ],
            msds_doc_id_used="seed_msds_99001_v4",
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
