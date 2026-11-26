"""Bilingual entity-name alias mapping routes (e.g. 中文持有人 <-> English LoA entity)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CompanyUserContext, get_current_company_user
from app.db.models import EntityAlias
from app.db.session import get_db_session

router = APIRouter(prefix="/api/entity-aliases", tags=["entity-aliases"])


class EntityAliasOut(BaseModel):
    id: int
    zh_name: str
    en_name: str
    confirmed: bool


class EntityAliasUpsertRequest(BaseModel):
    zh_name: str = Field(..., min_length=1, max_length=256)
    en_name: str = Field(..., min_length=1, max_length=256)
    confirmed: bool = True

    @field_validator("zh_name", "en_name")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Entity name must not be blank.")
        return value


@router.get("", response_model=list[EntityAliasOut])
async def list_entity_aliases(
    user: CompanyUserContext = Depends(get_current_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[EntityAliasOut]:
    rows = (
        await session.execute(
            select(EntityAlias)
            .where(EntityAlias.company_id == user.company_id)
            .order_by(EntityAlias.id.desc())
        )
    ).scalars().all()
    return [
        EntityAliasOut(id=r.id, zh_name=r.zh_name, en_name=r.en_name, confirmed=r.confirmed)
        for r in rows
    ]


@router.post("", response_model=EntityAliasOut)
async def upsert_entity_alias(
    body: EntityAliasUpsertRequest,
    user: CompanyUserContext = Depends(get_current_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> EntityAliasOut:
    zh = " ".join(body.zh_name.split())
    en = " ".join(body.en_name.split())
    existing = (
        await session.execute(
            select(EntityAlias).where(
                EntityAlias.company_id == user.company_id,
                EntityAlias.zh_name == zh,
                EntityAlias.en_name == en,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.confirmed = body.confirmed
        row = existing
    else:
        row = EntityAlias(
            company_id=user.company_id,
            zh_name=zh,
            en_name=en,
            confirmed=body.confirmed,
        )
        session.add(row)

    await session.commit()
    await session.refresh(row)
    return EntityAliasOut(id=row.id, zh_name=row.zh_name, en_name=row.en_name, confirmed=row.confirmed)
