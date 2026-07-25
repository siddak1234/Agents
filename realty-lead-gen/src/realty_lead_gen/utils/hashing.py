"""Perceptual hashing for photo dedup + caching."""

from __future__ import annotations

import hashlib
import io

from imagehash import dhash
from PIL import Image


def perceptual_hash(image_bytes: bytes) -> str:
    """Return a 16-char hex dhash for photo dedup."""
    img = Image.open(io.BytesIO(image_bytes))
    return str(dhash(img))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
