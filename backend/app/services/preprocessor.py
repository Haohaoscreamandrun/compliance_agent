# cSpell:words pymupdf webp exif LANCZOS
"""In-RAM PDF & JPEG/image pre-processor producing normalized WebP page images (ADR-001 Decision 3).

Raw uploaded PDFs and JPEGs are processed strictly in memory and never written to disk.
Only normalized <=1600px `.webp` page images are atomically written under `{storage_dir}/pages/{doc_id}/`.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

_SAFE_DOC_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
_ALLOWED_PIL_FORMATS = ("JPEG", "PNG", "WEBP", "TIFF")
_MAX_IMAGE_PIXELS = 50_000_000
_MAX_PDF_PAGES = 50
_MAX_PDF_ZOOM = 3.0  # Caps upscaling of small labels at ~216 DPI


@dataclass(frozen=True, slots=True)
class ProcessedPage:
    page_number: int
    image_rel_path: str
    width_px: int
    height_px: int
    webp_bytes: bytes


def _validate_doc_id(doc_id: str) -> str:
    if not _SAFE_DOC_ID.match(doc_id) or doc_id.upper() in _WINDOWS_RESERVED:
        raise ValueError(
            f"doc_id must match ^[A-Za-z0-9_-]{{1,64}}$ (non-reserved), got {doc_id!r}"
        )
    return doc_id


def _composite_alpha_onto_white(img: Image.Image) -> Image.Image:
    """Composite transparent images onto white so black text on transparent labels stays readable."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB") if img.mode != "RGB" else img


def _normalize_pil_to_webp(
    img: Image.Image,
    max_edge_px: int,
    webp_quality: int,
) -> tuple[bytes, int, int]:
    """Apply EXIF orientation, composite onto white RGB, downscale longest edge, and encode WebP."""
    try:
        transposed = ImageOps.exif_transpose(img)
    except Exception as exc:
        raise ValueError("Corrupted EXIF metadata in uploaded image.") from exc

    rgb = _composite_alpha_onto_white(transposed)
    width, height = rgb.size
    if width <= 0 or height <= 0:
        raise ValueError("Image has zero width or height.")

    longest = max(width, height)
    if longest > max_edge_px:
        scale = max_edge_px / longest
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        rgb = rgb.resize(new_size, Image.Resampling.LANCZOS)
        width, height = rgb.size

    buf = BytesIO()
    rgb.save(buf, format="WEBP", quality=webp_quality, method=4)
    return buf.getvalue(), width, height


def _render_pdf_pages_to_webp(
    raw_bytes: bytes,
    filename: str,
    max_edge_px: int,
    webp_quality: int,
) -> list[tuple[bytes, int, int]]:
    try:
        pdf = pymupdf.open(stream=raw_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Invalid or corrupted PDF upload: {filename!r}") from exc

    try:
        if pdf.needs_pass:
            raise ValueError("Encrypted or password-protected PDFs are not supported.")
        if pdf.page_count < 1:
            raise ValueError("PDF contains zero pages.")
        if pdf.page_count > _MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds maximum allowed page count ({_MAX_PDF_PAGES}).")

        rendered: list[tuple[bytes, int, int]] = []
        for page in pdf:
            rect = page.rect
            longest_pt = max(rect.width, rect.height, 1.0)
            # Scale directly to <= max_edge_px in C memory so giant MediaBox PDFs never OOM
            zoom = min(max_edge_px / longest_pt, _MAX_PDF_ZOOM)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            rendered.append(_normalize_pil_to_webp(pil_img, max_edge_px, webp_quality))
        return rendered
    finally:
        pdf.close()


def _render_image_frames_to_webp(
    raw_bytes: bytes,
    filename: str,
    max_edge_px: int,
    webp_quality: int,
) -> list[tuple[bytes, int, int]]:
    try:
        with Image.open(BytesIO(raw_bytes), formats=_ALLOWED_PIL_FORMATS) as img:
            w, h = img.size
            if w <= 0 or h <= 0 or (w * h) > _MAX_IMAGE_PIXELS:
                raise ValueError(f"Image dimensions ({w}x{h}) exceed safe pixel limit.")
            rendered: list[tuple[bytes, int, int]] = []
            for frame_idx, frame in enumerate(ImageSequence.Iterator(img), start=1):
                if frame_idx > _MAX_PDF_PAGES:
                    raise ValueError(f"Multi-page image exceeds {_MAX_PDF_PAGES} frames.")
                frame.load()
                rendered.append(_normalize_pil_to_webp(frame.copy(), max_edge_px, webp_quality))
            return rendered
    except ValueError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError(f"Invalid or unsafe image upload: {filename!r}") from exc


def preprocess_upload_in_memory(
    raw_bytes: bytes,
    filename: str,
    doc_id: str,
    storage_dir: Path,
    max_edge_px: int = 1600,
    webp_quality: int = 85,
) -> list[ProcessedPage]:
    """Render PDF pages or normalize an uploaded JPEG/image in RAM and write WebP pages atomically.

    Raw upload bytes are never persisted to disk.
    """
    _validate_doc_id(doc_id)
    if max_edge_px < 1 or not (1 <= webp_quality <= 100):
        raise ValueError("Invalid max_edge_px or webp_quality parameter.")
    if not raw_bytes:
        raise ValueError("Uploaded file is empty.")

    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        rendered = _render_pdf_pages_to_webp(raw_bytes, filename, max_edge_px, webp_quality)
    elif ext in _IMAGE_EXTS:
        rendered = _render_image_frames_to_webp(raw_bytes, filename, max_edge_px, webp_quality)
    else:
        raise ValueError(
            f"Unsupported file extension {ext!r}; expected .pdf or one of {sorted(_IMAGE_EXTS)}."
        )

    pages_root = (storage_dir / "pages").resolve()
    pages_root.mkdir(parents=True, exist_ok=True)
    target_dir = pages_root / doc_id
    staging_dir = pages_root / f".{doc_id}.staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    processed: list[ProcessedPage] = []
    try:
        for idx, (webp_bytes, width_px, height_px) in enumerate(rendered, start=1):
            (staging_dir / f"page_{idx}.webp").write_bytes(webp_bytes)
            processed.append(
                ProcessedPage(
                    page_number=idx,
                    image_rel_path=f"pages/{doc_id}/page_{idx}.webp",
                    width_px=width_px,
                    height_px=height_px,
                    webp_bytes=webp_bytes,
                )
            )
        if target_dir.exists():
            shutil.rmtree(target_dir)
        staging_dir.rename(target_dir)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    return processed
