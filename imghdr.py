"""
Compat shim for the removed stdlib module `imghdr` (removed in Python 3.13).

Provides a minimal `what()` implementation sufficient for common formats used
by python-telegram-bot 13.x when handling image uploads.

Supported types: 'png', 'jpeg', 'gif', 'bmp', 'webp', 'tiff'
"""

from __future__ import annotations

from typing import Optional, Union


def _detect_from_bytes(data: bytes) -> Optional[str]:
    if len(data) < 12:
        return None

    # PNG: 89 50 4e 47 0d 0a 1a 0a
    if data.startswith(b"\x89PNG\r\n\x1a\x0a"):
        return "png"

    # JPEG: FF D8 ... FF (EOI later); just check start
    if data.startswith(b"\xff\xd8"):
        return "jpeg"

    # GIF87a or GIF89a
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"

    # BMP: 'BM'
    if data.startswith(b"BM"):
        return "bmp"

    # WEBP: RIFF....WEBP
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"

    # TIFF little/big endian
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return "tiff"

    return None


def what(file: Union[str, bytes, bytearray, memoryview], h: Optional[bytes] = None) -> Optional[str]:
    """
    Return image type for given file or header `h`.

    - If `h` is provided (bytes-like), detect from it.
    - Else if `file` is a path-like (str), read a small header and detect.
    - Else if `file` is bytes-like, detect from it.
    """
    if h is not None:
        return _detect_from_bytes(h)

    if isinstance(file, (bytes, bytearray, memoryview)):
        return _detect_from_bytes(bytes(file))

    if isinstance(file, str):
        try:
            with open(file, "rb") as f:
                header = f.read(64)
            return _detect_from_bytes(header)
        except Exception:
            return None

    return None


