"""Outline-hash font identification for obfuscated/renamed subset fonts.

When a legacy Tibetan font is embedded in a PDF under an obfuscated PostScript
name (so the name-based pytiblegenc table lookup fails), we can still recover
the original font by hashing its glyph *outlines* and matching them against a
vendored glyph database (``data/pytiblegenc/glyph_db.csv``).

Both the database and the hashing here are ported from ``pytiblegenc``
(``pytiblegenc/font_utils.py``, ``get_glyph_hashes_from_bytes`` /
``identify_font``); the hash must stay byte-compatible with the values stored in
``glyph_db.csv``. A "match" is a database font whose glyph-hash set is a
*superset* of the (subset) embedded font's hashes -- the usual case for a
subsetted PDF font.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from fontTools.ttLib import TTFont

_GLYPH_DB = Path(__file__).resolve().parent / "data" / "pytiblegenc" / "glyph_db.csv"


def compute_glyph_hash(ttfont: "TTFont", glyph_name: str) -> Optional[str]:
    """Normalized SHA-256 of a glyph outline (ported from pytiblegenc).

    Returns ``None`` for empty glyphs (no contours): their pytiblegenc hash is
    keyed on the glyph *name*, which subsetters rewrite, so they are unreliable
    for matching and excluded here. Non-empty glyphs hash purely on normalized
    coordinates, so they survive subsetting/renaming.
    """
    if "glyf" not in ttfont:
        return None
    glyf = ttfont["glyf"]
    try:
        glyph = glyf[glyph_name]
        coords, end_pts, flags = glyph.getCoordinates(glyf)
        upem = ttfont["head"].unitsPerEm
    except Exception:
        return None
    if not coords or not upem:
        return None

    norm = [(x / upem, y / upem) for (x, y) in coords]
    min_x = min(p[0] for p in norm)
    min_y = min(p[1] for p in norm)
    norm = [(x - min_x, y - min_y) for (x, y) in norm]

    contour_ends = set(end_pts)
    parts: List[str] = []
    for i, (x, y) in enumerate(norm):
        on_curve = flags[i] & 1
        parts.append(f"{x:.6f},{y:.6f},{on_curve}")
        if i in contour_ends:
            parts.append("|")
    blob = ";".join(parts)
    return sha256(blob.encode("utf-8")).hexdigest()


def embedded_glyph_hashes(ttfont: "TTFont") -> frozenset[str]:
    """Set of non-empty glyph-outline hashes for an embedded font."""
    if "glyf" not in ttfont:
        return frozenset()
    hashes: set[str] = set()
    for glyph_name in ttfont["glyf"].keys():
        h = compute_glyph_hash(ttfont, glyph_name)
        if h is not None:
            hashes.add(h)
    return frozenset(hashes)


@lru_cache(maxsize=1)
def _font_hash_index() -> dict[str, frozenset[str]]:
    """``{font_postscript_name: frozenset(glyph_hash)}`` from the vendored DB."""
    acc: dict[str, set[str]] = {}
    if not _GLYPH_DB.is_file():
        return {}
    with _GLYPH_DB.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            glyph_hash = row.get("glyph_hash")
            ps_name = row.get("font_postscript_name")
            if not glyph_hash or not ps_name:
                continue
            acc.setdefault(ps_name, set()).add(glyph_hash)
    return {name: frozenset(hs) for name, hs in acc.items()}


@lru_cache(maxsize=1)
def _all_db_hashes() -> frozenset[str]:
    """Union of every glyph hash in the DB (for a fast non-membership cutoff)."""
    out: set[str] = set()
    for hs in _font_hash_index().values():
        out |= hs
    return frozenset(out)


@lru_cache(maxsize=1)
def glyph_lookup_tables() -> Tuple[
    dict[Tuple[str, int], str], dict[str, frozenset[Tuple[str, int]]]
]:
    """Ported from pytiblegenc ``build_glyph_lookup_tables``.

    Returns ``(forward_map, reverse_map)`` where
    ``forward_map[(font_name, codepoint)] = glyph_hash`` and
    ``reverse_map[glyph_hash] = {(font_name, codepoint), ...}``. These let a
    character that is missing from one font's conversion table be recovered from
    a *sibling* font whose glyph at some codepoint has the identical outline.
    """
    forward: dict[Tuple[str, int], str] = {}
    reverse: dict[str, set] = {}
    if not _GLYPH_DB.is_file():
        return {}, {}
    with _GLYPH_DB.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            glyph_hash = row.get("glyph_hash")
            ps_name = row.get("font_postscript_name")
            cp_str = row.get("codepoint")
            if not glyph_hash or not ps_name or not cp_str:
                continue
            try:
                cp = int(cp_str)
            except ValueError:
                continue
            forward[(ps_name, cp)] = glyph_hash
            reverse.setdefault(glyph_hash, set()).add((ps_name, cp))
    return forward, {h: frozenset(v) for h, v in reverse.items()}


def identify_candidates(ttfont: "TTFont") -> List[str]:
    """Candidate source PostScript names for an embedded (subset) font.

    Returns names whose database glyph-hash set is a superset of the embedded
    font's non-empty hashes, ordered from the tightest match (smallest glyph
    set) to the loosest, then by name. The empty list means "no confident
    identification".

    Fast path: hashing is interleaved with a membership check against the union
    of all DB hashes, so a font with even one outline absent from the DB (any
    non-legacy font, e.g. Times/Arial) bails after a single glyph instead of
    hashing the whole program.

    Corrupt or truncated embedded programs (a Distiller Wingdings subset with
    a broken ``cmap`` format 4, …) must not abort the whole PDF — they simply
    fail to identify.
    """
    try:
        if "glyf" not in ttfont:
            return []
        index = _font_hash_index()
        if not index:
            return []
        universe = _all_db_hashes()
        H: set[str] = set()
        for glyph_name in ttfont["glyf"].keys():
            h = compute_glyph_hash(ttfont, glyph_name)
            if h is None:
                continue
            if h not in universe:
                return []
            H.add(h)
        if not H:
            return []
        matches: list[tuple[int, str]] = []
        for ps_name, db_hashes in index.items():
            if H <= db_hashes:
                matches.append((len(db_hashes), ps_name))
        matches.sort(key=lambda t: (t[0], t[1]))
        return [name for _, name in matches]
    except Exception:
        return []
