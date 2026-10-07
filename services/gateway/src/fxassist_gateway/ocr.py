"""Text from an uploaded image, for the web page (ADR-027).

The image is decoded and checked by Pillow (type, pixel count), re-encoded as a plain grayscale
PNG, and read by the Tesseract command-line program in a subprocess with a timeout. The result
is plain text that the user sees and sends as part of a question, so it goes through exactly the
same guard as typed text (SAF-04): an image is untrusted input like any other (SAF-09).
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

ALLOWED_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


class OcrError(Exception):
    """An image that cannot be read; `code` becomes the API error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class OcrEngine(Protocol):
    def available(self) -> bool: ...

    def read(self, png: bytes) -> str: ...


@dataclass
class TesseractOcr:
    binary: str = "tesseract"
    language: str = "eng"
    timeout_s: float = 20.0

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def read(self, png: bytes) -> str:
        try:
            out = subprocess.run(  # noqa: S603 - fixed program and arguments, image on stdin
                [self.binary, "stdin", "stdout", "-l", self.language, "--psm", "3"],
                input=png,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise OcrError("ocr_timeout", "Reading the image took too long.") from None
        if out.returncode != 0:
            raise OcrError("invalid_image", "The image could not be read.")
        return out.stdout.decode("utf-8", errors="replace")


def prepare(data: bytes, content_type: str, *, max_pixels: int) -> bytes:
    """Check the image and re-encode it as grayscale PNG for OCR."""
    from PIL import Image, UnidentifiedImageError

    expected = ALLOWED_TYPES.get(content_type.split(";")[0].strip().lower())
    if expected is None:
        raise OcrError("unsupported_media_type", "Send a PNG, JPEG or WebP image.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != expected:  # the declared type must match the bytes
                raise OcrError("invalid_image", "The image does not match its declared type.")
            width, height = image.size
            if width * height > max_pixels:  # checked before decoding: no decompression bombs
                raise OcrError("image_too_large", f"The image has more than {max_pixels:,} pixels.")
            gray = image.convert("L")
            if width < 1000:  # small screenshots read better at twice the size
                gray = gray.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            gray.save(out, format="PNG")
            return out.getvalue()
    except OcrError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise OcrError("invalid_image", "The image could not be read.") from None


_SPACES = re.compile(r"[ \t\f\v]+")


def clean(text: str, *, max_chars: int) -> tuple[str, bool]:
    """Tidy OCR output: no control characters, single spaces, no blank runs; capped length."""
    text = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    tidy = "\n".join(line for line in lines if line)
    if len(tidy) <= max_chars:
        return tidy, False
    return tidy[:max_chars].rstrip(), True
