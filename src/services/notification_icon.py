"""Validation, normalisation and URL construction for uploaded notification icons.

Everything an operator uploads is decoded and re-encoded here before it reaches
the database. That is a security boundary, not a convenience:

* The bytes are served back from our own API origin. A file that is both a
  valid image and valid HTML/JavaScript (a "polyglot") would otherwise be one
  content-type sniff away from executing on our domain. Re-encoding from the
  decoded pixels throws away everything that is not pixels, including any
  trailing script, embedded HTML, ICC/EXIF blobs and PNG text chunks.
* SVG is refused outright. It is a document format with script support, and no
  amount of re-encoding makes serving operator-supplied SVG from our origin
  safe.
* The raw upload is size-capped before Pillow ever sees it, and the decoded
  pixel count is capped too, so a small file that expands to gigabytes of
  bitmap (a decompression bomb) is rejected rather than exhausting the worker.

The output is always a square-ish PNG of at most ``MAX_DIMENSION`` px, which is
the size Chrome actually draws for a notification icon. Storing the original
resolution would waste database bytes and push-service bandwidth for pixels
nobody sees.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Formats Pillow may decode from. Anything else — SVG, PDF, ICO, animated GIF —
# is rejected with a message naming what is allowed.
ALLOWED_INPUT_FORMATS = {"PNG", "JPEG", "WEBP"}

# What the browser is told the stored image is. Always PNG: we re-encode
# everything, so the input format has no bearing on what we serve.
STORED_CONTENT_TYPE = "image/png"

# Largest upload accepted, before decoding. Notification icons are small; this
# is generous enough for a photo straight off a phone and small enough that a
# malicious upload cannot tie up a worker.
MAX_UPLOAD_BYTES = int(os.getenv("NOTIFICATION_ICON_MAX_BYTES", str(2 * 1024 * 1024)))

# Chrome renders the notification icon at roughly 64-96 CSS px; 192 covers
# high-DPI without storing more than needed.
MAX_DIMENSION = int(os.getenv("NOTIFICATION_ICON_MAX_DIMENSION", "192"))

# Decompression-bomb guard: refuse anything whose declared canvas exceeds this,
# independent of how few bytes the compressed file is.
MAX_PIXELS = 40_000_000

# Absolute base the icon URL is built from. A push notification's icon is
# fetched by the customer's browser from whatever URL we put in the payload, so
# it must be publicly reachable — a relative path would resolve against the
# frontend origin, which does not serve this route. Falls back to the origin the
# upload arrived on, which makes local development work with no configuration.
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "").strip().rstrip("/")

ICON_ID_RE = re.compile(r"^[a-f0-9]{64}$")


class IconValidationError(ValueError):
    """Upload refused. The message is safe to show the operator verbatim."""


def is_valid_icon_id(value: Optional[str]) -> bool:
    """True for something shaped like one of our stored icon ids (sha256 hex)."""
    return bool(value) and bool(ICON_ID_RE.match(str(value).strip()))


def normalize_upload(raw: bytes, *, filename: Optional[str] = None) -> Tuple[str, bytes, int, int]:
    """Decode, shrink and re-encode an upload. Returns ``(id, png_bytes, w, h)``.

    Raises :class:`IconValidationError` for anything we will not store. The id
    is the sha256 of the *normalised* bytes, so two uploads that differ only in
    metadata collapse to the same row and the same cacheable URL.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise IconValidationError(
            "Зураг боловсруулах сан (Pillow) суулгаагүй байна."
        ) from exc

    if not raw:
        raise IconValidationError("Хоосон файл байна.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise IconValidationError(
            f"Файл хэт том байна ({len(raw) // 1024}KB). "
            f"Дээд хэмжээ {MAX_UPLOAD_BYTES // 1024}KB."
        )

    # Pillow's own bomb guard, set alongside our explicit check below so a
    # DecompressionBombError surfaces as a clean 400 rather than a 500.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt = (image.format or "").upper()
            if fmt not in ALLOWED_INPUT_FORMATS:
                raise IconValidationError(
                    f"{fmt or 'Тодорхойгүй'} формат дэмжигдэхгүй. "
                    "PNG, JPEG, WebP файл оруулна уу."
                )
            if image.width * image.height > MAX_PIXELS:
                raise IconValidationError("Зургийн нягтрал хэт өндөр байна.")

            # Load before converting so a truncated file fails here, where the
            # error is still ours to translate, rather than mid-save.
            image.load()

            # RGBA keeps transparency in logos; anything paletted or CMYK is
            # normalised on the way through.
            converted = image.convert("RGBA")

        converted.thumbnail((MAX_DIMENSION, MAX_DIMENSION), resample=Image.LANCZOS)

        buffer = io.BytesIO()
        # `optimize` costs a little CPU once per upload and saves bytes on every
        # single push delivery afterwards.
        converted.save(buffer, format="PNG", optimize=True)
        png = buffer.getvalue()
        width, height = converted.size
        converted.close()
    except IconValidationError:
        raise
    except UnidentifiedImageError as exc:
        raise IconValidationError(
            "Зураг таних боломжгүй байна. PNG, JPEG, WebP файл оруулна уу."
        ) from exc
    except Exception as exc:
        logger.warning("Notification icon upload failed to decode (%s)", filename, exc_info=True)
        raise IconValidationError("Зургийг боловсруулж чадсангүй.") from exc

    return hashlib.sha256(png).hexdigest(), png, width, height


def icon_url(icon_id: Optional[str], origin: Optional[str] = None) -> Optional[str]:
    """Absolute URL the service worker should load for this icon.

    ``PUBLIC_API_BASE_URL`` wins when set — that is the deployment's real public
    address. Without it we fall back to the origin recorded when the image was
    uploaded, which is right in development and right in production too as long
    as the admin panel talks to the same host customers do.
    """
    if not icon_id:
        return None
    base = PUBLIC_API_BASE_URL or (origin or "").rstrip("/")
    path = f"/api/push/icons/{icon_id}"
    return f"{base}{path}" if base else path


def safe_label(filename: Optional[str]) -> Optional[str]:
    """A display-only name for the picker tile.

    Path separators are stripped because this string is echoed back into the
    admin panel; it never touches the filesystem, and it must not be able to
    look like one.
    """
    if not filename:
        return None
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name[:120] or None
