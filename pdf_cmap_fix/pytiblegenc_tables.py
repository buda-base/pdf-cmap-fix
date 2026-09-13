"""Curated char -> Unicode conversion tables for legacy non-Unicode Tibetan fonts.

The base tables (``data/pytiblegenc/tiblegenc.csv`` and ``utfc.csv``) and the
font-name normalisation/alias rules are vendored from ``pytiblegenc``
(https://github.com/buda-base/pydeduff, ``pytiblegenc/char_converter.py``).
Book-reviewed additions live in ``reviewed_bugs4.csv``.

These legacy fonts (Ededris/Dedris, TibetanChogyal, LTibetan, Esam*, ...) carry
no usable GSUB/cmap, so pdf-cmap-fix's GSUB-derived lookups produce garbage for
them. Instead, the PDF's *existing* ToUnicode already yields a (wrong) Latin-ish
character per code; re-mapping that character's codepoint through the per-font
table here recovers the correct Unicode -- exactly how ``pytiblegenc`` converts
the text downstream.

This module is pure-Python with no third-party dependencies.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

# How error/unconvertible glyphs are encoded in the source tables.
ERROR_CHR = "\u0f20\u0f20\u0f20\u0f20"  # "༠༠༠༠"

_DATA_DIR = Path(__file__).resolve().parent / "data" / "pytiblegenc"
# tiblegenc.csv is the primary table; reviewed_bugs4.csv adds distinct,
# book-verified encodings plus a few targeted corrections; utfc.csv only fills
# remaining gaps (matches the base/utfc precedence in ``_convert_char``).
_TABLE_FILES = ("tiblegenc.csv", "reviewed_bugs4.csv", "utfc.csv")

# Vendored from pytiblegenc.char_converter.FONT_ALIASES.
FONT_ALIASES = {
    "Dedris-syma": "Ededris-sym",
    "Ededris-syma": "Ededris-sym",
    "TibetanClassicSkt": "TibetanClassicSkt1",
    "TibetanChogyalSkt": "TibetanChogyalSkt1",
    "TB-TTYoutso": "TB-Youtso",
    "TB2-TTYoutso": "TB2-Youtso",
    # TB-TTYoutso-Bold / TB2-TTYoutso-Bold deliberately have dedicated
    # reviewed tables. Their ancient-history encoding differs substantially
    # from TB-Youtso, despite the similar PostScript names.
    "TCRCYoutso": "TCRC Youtso",
    "TCRC-Bod-Yig": "TCRC Bod-Yig",
}


def normalize_font_name(font_name: str, weight: Optional[str] = None) -> str:
    """Vendored from pytiblegenc.char_converter.normalize_font_name.

    ``weight`` is ``"b"``, ``"i"`` or ``"bi"`` (only relevant to the legacy
    WordPerfect path; unused here but kept for parity).
    """
    if font_name in FONT_ALIASES:
        font_name = FONT_ALIASES[font_name]
    if font_name.startswith("Dedris"):
        font_name = "Ed" + font_name[1:]
    if font_name.startswith("Sam") and len(font_name) == 4:
        font_name = "Es" + font_name[1:]
    # Dzongkha Calligraphic shares the Tibetan Calligraphic encoding.
    # Stack slots are full precomposed syllables (Chogyal layout), not
    # attu's decomposed subjoined-only rows.
    if font_name.startswith("DzongkhaCalligraphic"):
        font_name = "Tibetan" + font_name[len("Dzongkha") :]
    # Tables use a space after the TCRC foundry (``TCRC Youtso``);
    # PDFs often glue or hyphenate it (``TCRCYoutso``, ``TCRC-Bod-Yig``).
    if font_name.startswith("TCRC") and len(font_name) > 4 and font_name[4] != " ":
        rest = font_name[4:]
        font_name = "TCRC " + rest[1:] if rest.startswith("-") else "TCRC " + rest
    # PostScript style names use a hyphenated weight suffix
    # (``TB-Youtso-Normal``). Stripping the bare ``Normal`` tail left a
    # dangling hyphen (``TB-Youtso-``) and missed the conversion table.
    if font_name.endswith("-Normal") or font_name.endswith(" Normal"):
        font_name = font_name[:-7].strip()
    elif font_name.endswith("Normal"):
        font_name = font_name[:-6].strip().rstrip("-").strip()
    if weight == "b":
        font_name += "Skt1"
    if weight == "i":
        font_name += "Skt2"
    if weight == "bi":
        font_name += "Skt3"
    return font_name


def _strip_subset_prefix(font_name: str) -> str:
    """Drop a 6-letter PDF subset tag, e.g. ``KSWSGG+Dedris-a`` -> ``Dedris-a``."""
    if "+" in font_name:
        font_name = font_name.split("+", 1)[1]
    return font_name.strip()


def _load_one(path: Path, base: dict[str, dict[str, str]]) -> None:
    """Merge one CSV into ``base`` (existing entries win, like pytiblegenc)."""
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh, quotechar='"'):
            # row[2] may be empty (glyph we deliberately don't convert).
            if len(row) < 3:
                continue
            font_name, cp_str, value = row[0], row[1], row[2]
            try:
                unicp = int(cp_str)
            except ValueError:
                continue
            fmap = base.setdefault(font_name, {})
            # cp1252 alias key for low codepoints (matches get_base_from_file).
            if unicp < 256:
                try:
                    alt = bytes([unicp]).decode("cp1252")
                except (ValueError, UnicodeDecodeError):
                    alt = None
                if alt is not None and alt not in fmap:
                    fmap[alt] = value
            key = chr(unicp)
            if key not in fmap:
                fmap[key] = value


@lru_cache(maxsize=1)
def _base() -> dict[str, dict[str, str]]:
    base: dict[str, dict[str, str]] = {}
    for name in _TABLE_FILES:
        path = _DATA_DIR / name
        if path.is_file():
            _load_one(path, base)
    return base


def table_for(font_name: str) -> Tuple[Optional[str], Optional[dict[str, str]]]:
    """Resolve a PDF font name to ``(normalised_name, char_map)`` or ``(None, None)``.

    Strips a subset prefix, applies pytiblegenc's normalisation/aliases, then
    looks up the per-font conversion table. If that fails, peels off a leading
    foundry/wrapper prefix delimited by ``_`` or ``.`` (e.g. the ``Gen_`` in
    ``Gen_TibetanChogyal`` / ``Gen.TibetanChogyalSkt1``) and retries. It only ever
    returns a name that is itself a known table, so this cannot produce a wrong
    match.
    """
    if not font_name:
        return None, None
    base = _base()
    rest = _strip_subset_prefix(font_name)
    while True:
        norm = normalize_font_name(rest)
        table = base.get(norm)
        if table is not None and len(norm) >= 4:
            return norm, table
        # Peel one leading wrapper token (delimited by '_' or '.') and retry.
        idx = min((rest.find(c) for c in ("_", ".") if c in rest), default=-1)
        if idx < 0:
            return None, None
        rest = rest[idx + 1:].strip()
        if not rest:
            return None, None


def table_for_candidates(
    candidates: list[str],
) -> Tuple[Optional[str], Optional[dict[str, str]]]:
    """Resolve the first outline-identified candidate name that has a table.

    ``candidates`` is ordered tightest-match-first (see
    :func:`pdf_cmap_fix.glyph_outline_id.identify_candidates`); we normalise
    each through the alias rules and return the first one backed by a
    conversion table, or ``(None, None)``.
    """
    base = _base()
    for cand in candidates:
        norm = normalize_font_name(_strip_subset_prefix(cand))
        table = base.get(norm)
        if table is not None:
            return norm, table
    return None, None


def _glyph_lookup_recover(font_name: str, ch: str) -> Optional[str]:
    """pytiblegenc ``_convert_char`` glyph-lookup fallback.

    When ``ch`` is absent from ``font_name``'s table, find the glyph at
    ``ord(ch)`` in that font (via the glyph DB) and borrow the mapping of a
    *sibling* font whose glyph has the same outline hash. Returns the recovered
    Unicode string, or ``None``.
    """
    from pdf_cmap_fix.glyph_outline_id import glyph_lookup_tables

    forward, reverse = glyph_lookup_tables()
    glyph_hash = forward.get((font_name, ord(ch)))
    if not glyph_hash:
        return None
    base = _base()
    # Deterministic order (the source uses an unordered set).
    for cand_font, cand_cp in sorted(reverse.get(glyph_hash, frozenset())):
        cand_table = base.get(cand_font)
        if cand_table is None:
            continue
        value = cand_table.get(chr(cand_cp))
        if value is not None and value != ERROR_CHR and value != "":
            return value
    return None


def convert_char(font_name: str, ch: str) -> Optional[str]:
    """Map a single character for ``font_name`` (already normalised).

    Mirrors pytiblegenc ``_convert_char``: direct table hit first, then the
    glyph-outline sibling fallback. Returns the replacement string (always
    non-empty), or ``None`` when the character is an error sentinel, maps to the
    empty string (a glyph the table deliberately drops, e.g. spacing), or cannot
    be recovered at all.
    """
    table = _base().get(font_name)
    if table is not None:
        value = table.get(ch)
        if value is not None:
            # Present but flagged unconvertible: no glyph fallback (matches the
            # source, which only falls back when the char is absent entirely).
            if value == ERROR_CHR or value == "":
                return None
            return value
    return _glyph_lookup_recover(font_name, ch)


def convert_text(font_name: str, text: str) -> Optional[str]:
    """Re-map every character of an existing ToUnicode value for ``font_name``.

    Returns the concatenated Unicode string, or ``None`` if any character is
    unconvertible (so the caller keeps the existing mapping for that code).
    """
    parts: list[str] = []
    for ch in text:
        value = convert_char(font_name, ch)
        if value is None:
            return None
        parts.append(value)
    out = "".join(parts)
    return out or None
