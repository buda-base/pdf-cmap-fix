"""HuaGuang (华光) Windows/DOS Tibetan encoding → Unicode.

HuaGuang books are not the Ededris/Chogyal/Classic faces the GID and
pytiblegenc paths already handle. They encode Tibetan as GB-style 2-byte
slots (lead ``0xB0``–``0xFB``, Windows trail ``0xA1``–``0xFE``):

* **Founder PDFGenerator** splits the pair across Type1 *plane fonts*
  (``JBHGZWHZ.B6``, ``JBHGZWBZ-B0``, ``JBHGZWHZ_BA``). The lead byte is
  the name suffix; the trail byte is the ``Lxx`` glyph name (hex).
* **Acrobat Distiller** embeds one Type0/Identity-H face and writes each
  GB pair into ``/ToUnicode`` as the corresponding CJK ideograph
  (``啊`` = ``B0 A1`` = ``ཀ``).

The conversion table is UTFC's ``Hg2Uni.tbl`` (Trace Foundation, GPL-3.0).
Index (from ``Converter.c``)::

    94 * (lead - 0xB0) + (trail_dos - 0x21)

Windows trail = DOS trail + ``0x80``, so the Windows form is
``94 * (lead - 0xB0) + (trail_win - 0xA1)``. Empty slots are the
sentinel ``4046``; stacked syllables are concatenated 4-digit decimals.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).resolve().parent / "data" / "huaguang"
_TABLE_PATH = _DATA_DIR / "Hg2Uni.tbl"

# UTFC empty-slot sentinel (not a Unicode scalar we ever emit).
_EMPTY = 4046

# HuaGuang Windows lead / trail ranges (DOS trail is 0x21-0x7E).
_LEAD_MIN, _LEAD_MAX = 0xB0, 0xFB
_WIN_TRAIL_MIN, _WIN_TRAIL_MAX = 0xA1, 0xFE
_DOS_TRAIL_MIN, _DOS_TRAIL_MAX = 0x21, 0x7E

# After dropping a PDF subset tag: JBHGZWHZ.B6, JBHGZWBZ-B0, ZWBZ, FHYW1, FHZW1.
_HG_NAME_RE = re.compile(
    r"(?:JBHGZWHZ|JBHGZWBZ|ZWBZ|FHYW\d*|FHZW\d*)",
    re.IGNORECASE,
)
# HuaGuang English / Chinese symbol libraries (FHYW*, FHZW*). Distiller
# writes their GB slots as CJK ToUnicode; Hg2Uni then turns page numbers
# into stacked letters and dotted leaders into U+0F45. Confirmed on the
# Tsongkhapa TOC (visual "(1)" / leader dots) and Rongtha FHYW1-B0 /
# FHZW1-B0 glyph names.
_HG_SYMBOL_RE = re.compile(r"(?:FHYW\d*|FHZW\d*)", re.IGNORECASE)
_PLANE_RE = re.compile(r"[._-]([0-9A-Fa-f]{2})$")
_TRAIL_RE = re.compile(r"^L([0-9A-Fa-f]{2})$", re.IGNORECASE)

# FHYW1 plane B0: ASCII digits and the punctuation used as TOC page refs.
_FHYW_TRAIL: dict[int, str] = {
    0xC9: "(",
    0xCA: ")",
    0xCE: "-",
}
_FHYW_TRAIL.update({0xD1 + i: str(i) for i in range(10)})

# FHZW1 plane B0: filled TOC leader dot (one glyph, repeated).
_FHZW_TRAIL: dict[int, str] = {
    0xA5: "\u00b7",
}


def _strip_subset_prefix(name: str) -> str:
    return name.split("+", 1)[-1].strip() if name else ""


def is_huaguang_font(name: str) -> bool:
    """True for HuaGuang / Founder plane faces and Distiller companions."""
    return bool(name) and _HG_NAME_RE.search(_strip_subset_prefix(name)) is not None


def is_huaguang_symbol_font(name: str) -> bool:
    """True for FHYW* (English symbols) and FHZW* (Chinese symbols)."""
    return bool(name) and _HG_SYMBOL_RE.search(_strip_subset_prefix(name)) is not None


def plane_from_font_name(name: str) -> Optional[int]:
    """Lead byte encoded in a Founder plane suffix (``.B6``, ``-B0``, ``_BA``)."""
    base = _strip_subset_prefix(name)
    m = _PLANE_RE.search(base)
    if not m:
        return None
    lead = int(m.group(1), 16)
    if _LEAD_MIN <= lead <= _LEAD_MAX:
        return lead
    return None


def trail_from_glyph_name(gname: str) -> Optional[int]:
    """Trail byte from a HuaGuang Type1 name such as ``LA1`` / ``LF4``."""
    m = _TRAIL_RE.match(gname or "")
    if not m:
        return None
    trail = int(m.group(1), 16)
    if (
        _WIN_TRAIL_MIN <= trail <= _WIN_TRAIL_MAX
        or _DOS_TRAIL_MIN <= trail <= _DOS_TRAIL_MAX
    ):
        return trail
    return None


def _decode_slot_token(tok: str) -> Optional[str]:
    if tok == str(_EMPTY):
        return None
    chars: list[str] = []
    for i in range(0, len(tok), 4):
        chunk = tok[i : i + 4]
        if len(chunk) < 4:
            break
        cp = int(chunk)
        if cp == _EMPTY:
            continue
        chars.append(chr(cp))
    return "".join(chars) or None


@lru_cache(maxsize=1)
def _slots() -> tuple[Optional[str], ...]:
    text = _TABLE_PATH.read_text(encoding="ascii")
    return tuple(_decode_slot_token(tok) for tok in text.split())


def lookup(lead: int, trail: int) -> Optional[str]:
    """Map a HuaGuang (lead, trail) pair to Unicode, or ``None``."""
    if _WIN_TRAIL_MIN <= trail <= _WIN_TRAIL_MAX:
        idx = 94 * (lead - _LEAD_MIN) + (trail - _WIN_TRAIL_MIN)
    elif _DOS_TRAIL_MIN <= trail <= _DOS_TRAIL_MAX:
        idx = 94 * (lead - _LEAD_MIN) + (trail - _DOS_TRAIL_MIN)
    else:
        return None
    slots = _slots()
    if 0 <= idx < len(slots):
        return slots[idx]
    return None


def lookup_symbol(basename: str, lead: int, trail: int) -> Optional[str]:
    """Map a FHYW/FHZW (lead, trail) pair to ASCII / punctuation.

    Only the slots attested in the Founder/Distiller books are listed;
    unknown symbol slots stay unmapped (do *not* fall back to Hg2Uni).
    """
    if not is_huaguang_symbol_font(basename):
        return None
    base = _strip_subset_prefix(basename).upper()
    if "FHYW" in base:
        return _FHYW_TRAIL.get(trail)
    if "FHZW" in base:
        return _FHZW_TRAIL.get(trail)
    return None


def _lookup_for_font(basename: str, lead: int, trail: int) -> Optional[str]:
    if is_huaguang_symbol_font(basename):
        return lookup_symbol(basename, lead, trail)
    return lookup(lead, trail)


def from_cjk_char(text: str, font_name: str = "") -> Optional[str]:
    """Decode Distiller ToUnicode CJK (GB-encoded HuaGuang pair) to Tibetan.

    Each character is encoded as GBK; a 2-byte result is treated as a
    Windows (lead, trail) pair. Returns ``None`` when any character is
    not a convertible GB pair (ASCII, already-Tibetan, …).
    """
    if not text:
        return None
    parts: list[str] = []
    for ch in text:
        try:
            raw = ch.encode("gbk")
        except UnicodeEncodeError:
            return None
        if len(raw) != 2:
            return None
        uni = _lookup_for_font(font_name, raw[0], raw[1])
        if not uni:
            return None
        parts.append(uni)
    return "".join(parts) or None


def tounicode_from_plane_encoding(
    basename: str,
    encoding: dict[int, str],
) -> Optional[dict[int, str]]:
    """Build ``{char_code: Unicode}`` for a Founder Type1 plane font."""
    lead = plane_from_font_name(basename)
    if lead is None or not encoding:
        return None
    out: dict[int, str] = {}
    for code, gname in encoding.items():
        trail = trail_from_glyph_name(gname)
        if trail is None:
            continue
        uni = _lookup_for_font(basename, lead, trail)
        if uni:
            out[int(code)] = uni
    return out or None


def tounicode_from_cjk_map(
    existing: dict[int, str],
    font_name: str = "",
) -> Optional[dict[int, str]]:
    """Remap a Distiller CJK ToUnicode through the HuaGuang / symbol table."""
    if not existing:
        return None
    out: dict[int, str] = {}
    for code, val in existing.items():
        if not val:
            continue
        uni = from_cjk_char(val, font_name)
        if uni:
            out[int(code)] = uni
    return out or None
