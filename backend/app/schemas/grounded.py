# cSpell:words ymin xmin ymax xmax
"""Chain-of-Grounding schema primitives for Gemini constrained decoding (ADR-001 Decision 3)."""

from __future__ import annotations

import math
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")

Coord0To1000 = Annotated[int, Field(ge=0, le=1000)]

GhsPictogramCode = Literal[
    "GHS01",
    "GHS02",
    "GHS03",
    "GHS04",
    "GHS05",
    "GHS06",
    "GHS07",
    "GHS08",
    "GHS09",
]


# NOTE: Intentionally NO class docstring on GroundedField — Pydantic v2 emits class docstrings
# as JSON Schema `description`, and `google-genai` `_transformers.process_schema()` overwrites
# parent field descriptions when inlining `$ref` for `GroundedField[T] | None`.
#
# Field order is load-bearing (Chain-of-Grounding: verbatim_text -> value -> page -> box_2d):
# Gemini constrained decoding generates JSON keys in schema order, anchoring `box_2d`
# to the exact tokens transcribed in `verbatim_text` and `value`.
class GroundedField(BaseModel, Generic[T]):
    verbatim_text: str = Field(
        min_length=1,
        description="Exact verbatim text (or visual symbol description) as printed on the page.",
    )
    value: T = Field(
        description="Normalized canonical value (e.g., ISO date YYYY-MM-DD, trimmed entity name, or GHS code).",
    )
    page: int = Field(
        ge=1,
        description="1-based global page number from the '=== PAGE N ===' marker.",
    )
    box_2d: list[Coord0To1000] = Field(
        min_length=4,
        max_length=4,
        description="Tight 2D bounding box [ymin, xmin, ymax, xmax] normalized to 0-1000 on that page.",
    )

    @field_validator("verbatim_text")
    @classmethod
    def _require_non_blank_verbatim(cls, raw: str) -> str:
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError("verbatim_text must not be empty or whitespace-only.")
        return cleaned

    @field_validator("value")
    @classmethod
    def _require_non_blank_string_value(cls, raw: T) -> T:
        if isinstance(raw, str):
            cleaned = raw.strip()
            if not cleaned:
                raise ValueError("value must not be empty or whitespace-only.")
            return cleaned  # type: ignore[return-value]
        return raw

    @field_validator("box_2d", mode="before")
    @classmethod
    def _normalize_and_validate_box_2d(cls, raw: object) -> list[int]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            raise ValueError("box_2d must be a 4-element [ymin, xmin, ymax, xmax] sequence.")
        coords: list[int] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError("box_2d coordinates must be numeric.")
            if not math.isfinite(item):
                raise ValueError("box_2d coordinates must be finite numbers.")
            if item < -50 or item > 1050:
                raise ValueError(
                    f"box_2d coordinate {item} is outside 0-1000 normalized range (possible unnormalized pixel coordinate)."
                )
            coords.append(max(0, min(1000, round(item))))

        ymin, xmin, ymax, xmax = coords
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        if ymin >= ymax or xmin >= xmax:
            raise ValueError(
                f"box_2d must have positive area (ymin < ymax and xmin < xmax), got {[ymin, xmin, ymax, xmax]}."
            )
        return [ymin, xmin, ymax, xmax]
