"""Minimal imghdr replacement for Python 3.13 compatibility."""

import typing as _t


def what(file: _t.Union[str, bytes, "typing.IO[bytes]"], h: _t.Optional[bytes] = None) -> _t.Optional[str]:
    if h is None:
        if isinstance(file, (bytes, bytearray)):
            h = bytes(file)
        elif hasattr(file, "read"):
            pos = file.tell()
            try:
                h = file.read(32)
            finally:
                file.seek(pos)
        else:
            with open(file, "rb") as f:  # type: ignore[arg-type]
                h = f.read(32)

    if not h:
        return None

    for test in _TESTS:
        res = test(h)
        if res:
            return res
    return None


def _test_jpeg(h: bytes) -> _t.Optional[str]:
    if h[6:10] in (b"JFIF", b"Exif"):
        return "jpeg"
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def _test_png(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return None


def _test_gif(h: bytes) -> _t.Optional[str]:
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return None


def _test_tiff(h: bytes) -> _t.Optional[str]:
    if h.startswith((b"MM\x00\x2a", b"II\x2a\x00")):
        return "tiff"
    return None


def _test_bmp(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"BM"):
        return "bmp"
    return None


def _test_webp(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return "webp"
    return None


def _test_pbm(h: bytes) -> _t.Optional[str]:
    if h.startswith((b"P1", b"P4")):
        return "pbm"
    return None


def _test_pgm(h: bytes) -> _t.Optional[str]:
    if h.startswith((b"P2", b"P5")):
        return "pgm"
    return None


def _test_ppm(h: bytes) -> _t.Optional[str]:
    if h.startswith((b"P3", b"P6")):
        return "ppm"
    return None


def _test_rast(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"\x59\xa6\x6a\x95"):
        return "rast"
    return None


def _test_xbm(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"#define "):
        return "xbm"
    return None


def _test_iff(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"FORM") and h[8:12] == b"ILBM":
        return "iff"
    return None


def _test_pcx(h: bytes) -> _t.Optional[str]:
    if h[:1] == b"\x0a":
        return "pcx"
    return None


def _test_eps(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"%!PS"):
        return "eps"
    return None


def _test_tga(h: bytes) -> _t.Optional[str]:
    if len(h) >= 18 and h[1:2] in (b"\x00", b"\x01") and h[2:3] in (b"\x01", b"\x02", b"\x03"):
        return "tga"
    return None


def _test_sgi(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"\x01\xda"):
        return "sgi"
    return None


def _test_exr(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"\x76\x2f\x31\x01"):
        return "exr"
    return None


def _test_heif(h: bytes) -> _t.Optional[str]:
    if h[4:8] == b"ftyp" and h[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
        return "heif"
    return None


def _test_pdf(h: bytes) -> _t.Optional[str]:
    if h.startswith(b"%PDF"):
        return "pdf"
    return None


_TESTS = [
    _test_jpeg,
    _test_png,
    _test_gif,
    _test_tiff,
    _test_bmp,
    _test_webp,
    _test_pbm,
    _test_pgm,
    _test_ppm,
    _test_rast,
    _test_xbm,
    _test_iff,
    _test_pcx,
    _test_eps,
    _test_tga,
    _test_sgi,
    _test_exr,
    _test_heif,
    _test_pdf,
]
