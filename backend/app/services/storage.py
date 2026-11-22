# cSpell:words boto3 botocore webp
"""Dual-mode page image storage: local disk in dev/pytest, Cloudflare R2 (S3 API) in production."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import Settings
from app.services.preprocessor import ProcessedPage, validate_doc_id


class PageStorage:
    """Persist and retrieve normalized WebP page images on local disk or Cloudflare R2."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.r2_bucket.strip()
        self.endpoint_url = settings.r2_endpoint_url.strip()

    @property
    def uses_r2(self) -> bool:
        return bool(self.bucket and self.endpoint_url)

    def _s3_client(self) -> Any:
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.settings.r2_access_key_id.strip(),
            aws_secret_access_key=self.settings.r2_secret_access_key.strip(),
            region_name="auto",
        )

    def save_pages(self, doc_id: str, pages: list[ProcessedPage]) -> None:
        """Upload rendered WebP pages to R2 when configured (disk files already staged by preprocessor)."""
        safe_id = validate_doc_id(doc_id)
        if not self.uses_r2:
            return
        client = self._s3_client()
        for page in pages:
            client.put_object(
                Bucket=self.bucket,
                Key=page.image_rel_path,
                Body=page.webp_bytes,
                ContentType="image/webp",
                CacheControl="public, max-age=86400, immutable",
            )
        # Purge ephemeral container disk copy once safely uploaded to R2
        shutil.rmtree(self.settings.storage_dir / "pages" / safe_id, ignore_errors=True)

    def read_page(self, doc_id: str, rel_path: str) -> bytes | None:
        """Read normalized WebP bytes from R2 or local disk."""
        safe_id = validate_doc_id(doc_id)
        if self.uses_r2:
            try:
                obj = self._s3_client().get_object(Bucket=self.bucket, Key=rel_path)
                return obj["Body"].read()
            except ClientError:
                return None

        storage_root = self.settings.storage_dir.resolve()
        doc_dir = (storage_root / "pages" / safe_id).resolve()
        image_path = (storage_root / rel_path).resolve()
        if not image_path.is_relative_to(doc_dir) or not image_path.is_file():
            return None
        return Path(image_path).read_bytes()

    def delete_doc_pages(self, doc_id: str) -> None:
        """Remove all rendered WebP pages for `doc_id` from R2 or local disk."""
        safe_id = validate_doc_id(doc_id)
        shutil.rmtree(self.settings.storage_dir / "pages" / safe_id, ignore_errors=True)
        if not self.uses_r2:
            return
        client = self._s3_client()
        prefix = f"pages/{safe_id}/"
        listed = client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        keys = [{"Key": item["Key"]} for item in listed.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
