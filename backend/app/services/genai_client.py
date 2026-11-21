# cSpell:words genai vertexai
"""Unified google-genai Client factory supporting Paid AI Studio keys and GCP Vertex AI."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from google import genai
from google.genai import types

from app.config import Settings


@contextmanager
def _isolated_genai_env(use_vertexai: bool) -> Iterator[None]:
    """Prevent google-genai Client() from reading conflicting fallback env vars."""
    conflict_keys = (
        ("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if use_vertexai
        else ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")
    )
    saved = {k: os.environ.pop(k) for k in conflict_keys if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def build_genai_client(settings: Settings) -> genai.Client:
    """Create a google-genai Client using mutually-exclusive credentials from Settings."""
    kwargs = settings.validate_genai_credentials()
    kwargs["http_options"] = types.HttpOptions(
        timeout=20_000,
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    with _isolated_genai_env(settings.google_genai_use_vertexai):
        return genai.Client(**kwargs)
