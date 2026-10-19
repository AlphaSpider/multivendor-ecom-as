"""
Uploaded image validation and sanitisation.

Security posture
----------------
* The **file extension and declared content type are not trusted**: Pillow must be
  able to fully decode the file as an image before it is accepted.
* Size and pixel-count limits are enforced *before* decode, to blunt
  decompression bombs.
* Optional re-encoding discards EXIF (including GPS coordinates that would leak a
  seller's home location), embedded scripts and polyglot payloads.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("manzilcart.core.images")

# Guard against decompression bombs (a 20k x 20k PNG is ~1.6 GB of raw bitmap).
Image.MAX_IMAGE_PIXELS = 40_000_000  # ~40 megapixels

ACCEPTED_PILLOW_FORMATS = {"JPEG", "PNG", "WEBP"}
MIN_DIMENSION = 200
MAX_DIMENSION = 6000

FORMAT_TO_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
FORMAT_TO_EXTENSION = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


@dataclass(frozen=True)
class ImageInfo:
    """Details of a validated image."""

    width: int
    height: int
    pillow_format: str
    mime_type: str
    extension: str


def _extension_of(name: str) -> str:
    if "." not in (name or ""):
        return ""
    return name.rsplit(".", 1)[-1].lower()


def validate_image_upload(uploaded_file) -> ImageInfo:
    """
    Validate an ``InMemoryUploadedFile``/``TemporaryUploadedFile``.

    Returns:
        ImageInfo: decoded dimensions and a safe canonical format.

    Raises:
        ValidationError: when the file is too large, not an accepted format, is not
            actually an image, or has unreasonable dimensions.
    """
    if uploaded_file is None:
        raise ValidationError("Please choose an image file.")

    max_bytes = settings.MAX_IMAGE_UPLOAD_SIZE
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > max_bytes:
        raise ValidationError(
            f"Images must be {max_bytes // (1024 * 1024)} MB or smaller."
        )
    if size == 0:
        raise ValidationError("That file is empty.")

    extension = _extension_of(getattr(uploaded_file, "name", ""))
    if extension not in settings.ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(f".{item}" for item in settings.ALLOWED_IMAGE_EXTENSIONS)
        raise ValidationError(f"Unsupported file type. Allowed types: {allowed}.")

    declared_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    if declared_type and declared_type not in settings.ALLOWED_IMAGE_MIME_TYPES:
        raise ValidationError("Unsupported image type.")

    # Decode from a private copy so the caller's file pointer is untouched.
    uploaded_file.seek(0)
    payload = uploaded_file.read()
    uploaded_file.seek(0)

    try:
        with Image.open(io.BytesIO(payload)) as probe:
            probe.verify()  # detects truncated / malformed files
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("That file is not a readable image.") from exc

    try:
        with Image.open(io.BytesIO(payload)) as image:
            pillow_format = (image.format or "").upper()
            width, height = image.size
            # Force a full decode; Pillow's verify() does not catch everything.
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("That file is not a readable image.") from exc

    if pillow_format not in ACCEPTED_PILLOW_FORMATS:
        raise ValidationError(
            "Unsupported image format. Please upload a JPEG, PNG or WebP image."
        )

    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        raise ValidationError(
            f"Images must be at least {MIN_DIMENSION}x{MIN_DIMENSION} pixels."
        )
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValidationError(
            f"Images must be at most {MAX_DIMENSION}x{MAX_DIMENSION} pixels."
        )

    mime_type = FORMAT_TO_MIME[pillow_format]
    if declared_type and declared_type != mime_type and not (
        declared_type == "image/jpg" and mime_type == "image/jpeg"
    ):
        # The bytes are a valid image but of a different type than claimed.
        raise ValidationError("The file content does not match its file type.")

    return ImageInfo(
        width=width,
        height=height,
        pillow_format=pillow_format,
        mime_type=mime_type,
        extension=FORMAT_TO_EXTENSION[pillow_format],
    )


def sanitise_image(payload: bytes, *, max_dimension: int = 2000) -> tuple[bytes, str]:
    """
    Re-encode image bytes, stripping all metadata and capping the longest edge.

    Returns:
        ``(bytes, extension)`` ready to be written to storage as a fresh file.
    """
    try:
        with Image.open(io.BytesIO(payload)) as image:
            pillow_format = (image.format or "").upper()
            image.load()
            if pillow_format not in ACCEPTED_PILLOW_FORMATS:
                raise ValidationError("Unsupported image format.")

            # Flatten transparency for JPEG output.
            if pillow_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            longest = max(image.size)
            if longest > max_dimension:
                ratio = max_dimension / float(longest)
                new_size = (
                    max(1, int(image.size[0] * ratio)),
                    max(1, int(image.size[1] * ratio)),
                )
                image = image.resize(new_size, Image.LANCZOS)

            output = io.BytesIO()
            save_kwargs: dict = {"optimize": True}
            if pillow_format == "JPEG":
                save_kwargs |= {"quality": 85, "progressive": True}
            image.save(output, format=pillow_format, **save_kwargs)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("That file is not a readable image.") from exc

    return output.getvalue(), FORMAT_TO_EXTENSION[pillow_format]
