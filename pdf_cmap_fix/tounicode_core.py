"""
Shared ToUnicode merge logic for tier-specific CLIs (gid / gname / gshape).

``collect_font_merges(..., lookup_dir=..., tier=...)`` only uses JSON files whose
``_meta.lookup_kind`` matches ``tier`` (``gid`` also accepts absent ``lookup_kind``).
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Tuple

import fitz
from fontTools.ttLib import TTFont

from pdf_cmap_fix.content_streams import collect_referenced_gids
from pdf_cmap_fix.pdf_font_encoding import resolve_simple_encoding

LookupTier = Literal["gid", "gname", "gshape"]
StrategySpec = tuple[LookupTier, Path]

# Word PDFs may emit these Type0 GIDs before stack syllables; they are not
# always picked up by :func:`collect_referenced_gids` but still need ToUnicode
# entries (often supplied via lookup JSON, e.g. microsofthimalaya.json).
WORD_SENTINEL_GIDS: frozenset[int] = frozenset({0xFEFF, 0xFFFD})

# How many contradicted Tibetan entries a tier-1 GID map must produce -- with no
# corroboration at all -- before we treat it as keyed to a different glyph order
# and drop it. See :func:`_gid_map_corroborated`.
MIN_GID_CONFLICTS = 4

# Word sometimes wraps real Tj glyph text in marked-content spans with
# ActualText<FEFFFFFD>. PyMuPDF includes that ActualText in extraction,
# producing visible U+FFFD noise even when the following GIDs map fine.
#
# Example shape:
#   /Span<</ActualText<FEFFFFFD>>> BDC
#   ... <0384>Tj ...
#   EMC
#
# We preserve the inner drawing operators and only remove the BDC/EMC wrapper.
_WORD_ACTUALTEXT_SENTINEL_RE = re.compile(
    rb"/Span\s*<<.*?/ActualText\s*<\s*FEFFFFFD\s*>.*?>>\s*BDC\s*(.*?)\s*EMC",
    re.IGNORECASE | re.DOTALL,
)

# Explicit Unicode glyph names: uniXXXX, uniXXXXYYYY (AGL ligature groups),
# or uXXXX / uXXXXX / uXXXXXX. Used when a simple TrueType subset has no
# /Encoding but kept these names in its cmap (Quartz / Affinity).
_UNI_GLYPH_NAME_RE = re.compile(
    r"^(?:uni(?:[0-9A-Fa-f]{4})+|u[0-9A-Fa-f]{4,6})$"
)

PREVIEW_LINES = 15
PREVIEW_DIFF = 8
TIBETAN_RANGE = (0x0F00, 0x0FFF)

# PyMuPDF garbage levels 3 and 4 merge duplicate objects (4 also compares
# stream bytes). On a 300-page tagged PDF with tens of thousands of xrefs
# that is ~100s natively and effectively hangs the in-browser Pyodide
# worker, for a ~1% size win. Level 2 still drops unused objects and
# compacts the xref after we rewrite ToUnicode streams.
_PDF_SAVE_KW = dict(garbage=2, deflate=True)


def _strip_prefix(name: str) -> str:
    return name.split("+", 1)[1] if "+" in name else name


def _decode_pdf(s: str) -> str:
    return re.sub(
        r"#([0-9A-Fa-f]{2})",
        lambda m: chr(int(m.group(1), 16)),
        s,
    )


def _normalise_name(name: str) -> str:
    name = _strip_prefix(name)
    for _ in range(3):
        d = _decode_pdf(name)
        if d == name:
            break
        name = d
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _build_db_index(font_keys: Iterable[str]) -> dict:
    return {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in font_keys}


def _pick_best_font_key(db_index: dict, pdf_basename: str) -> Optional[str]:
    """Return the font key that best matches a PDF base font name."""
    pdf_key = _normalise_name(pdf_basename)
    best_key: Optional[str] = None
    best_score, best_delta = 0, 10**9
    for db_norm, db_key in db_index.items():
        if pdf_key == db_norm:
            score = 3
        elif pdf_key in db_norm:
            score = 2
        elif db_norm in pdf_key:
            score = 1
        else:
            continue
        delta = abs(len(db_norm) - len(pdf_key))
        if score > best_score or (score == best_score and delta < best_delta):
            best_score, best_delta, best_key = score, delta, db_key
    return best_key


def _discover_lookup_keys(lookup_dir: Path) -> list[str]:
    if not lookup_dir.is_dir():
        return []
    return sorted(
        p.stem for p in lookup_dir.glob("*.json") if p.name != "_manifest.json"
    )


def _load_lookup_file(path: Path) -> Optional[Tuple[str, dict[str, str]]]:
    """Load one lookup JSON. Returns ``(lookup_kind, inner)`` or ``None``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    meta = data.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    kind = meta.get("lookup_kind", "gid")
    if kind not in ("gid", "gname", "gshape"):
        kind = "gid"
    inner: Optional[dict[str, str]] = None
    for k, v in data.items():
        if k == "_meta" or not isinstance(v, dict) or not v:
            continue
        inner = {}
        for k2, u in v.items():
            if not isinstance(u, str):
                continue
            inner[str(k2)] = u
        break
    if not inner:
        return None
    return (kind, inner)


def _gid_map_from_inner(inner: dict[str, str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for g, u in inner.items():
        try:
            out[int(g)] = u
        except (ValueError, TypeError):
            continue
    return out


def _resolve_db_gid_map(
    doc: fitz.Document,
    font_xref: int,
    lookup_kind: str,
    inner: dict[str, str],
) -> dict[int, str]:
    """Map PDF GIDs to Unicode using embedded font + gname or gshape table."""
    if lookup_kind == "gid":
        return _gid_map_from_inner(inner)
    ext_font: Optional[TTFont] = None
    try:
        try:
            tup = doc.extract_font(font_xref)
        except Exception:
            return {}
        if not tup or len(tup) < 4:
            return {}
        buf = tup[3]
        if not buf or not isinstance(buf, (bytes, bytearray)):
            return {}
        ext_font = TTFont(io.BytesIO(bytes(buf)), lazy=False)
        go = ext_font.getGlyphOrder()
        resolved: dict[int, str] = {}
        n = min(len(go), 0x10000)
        if lookup_kind == "gshape":
            from pdf_cmap_fix.glyph_fingerprint import fingerprint_glyph
        for gid in range(n):
            gname = go[gid]
            if lookup_kind == "gname":
                u = inner.get(gname)
                if u:
                    resolved[gid] = u
            elif lookup_kind == "gshape":
                h = fingerprint_glyph(ext_font, gname)
                if h and h in inner:
                    resolved[gid] = inner[h]
        return resolved
    except Exception:
        return {}
    finally:
        if ext_font is not None:
            try:
                ext_font.close()
            except Exception:
                pass


# Simple (non-Type0) PDF font types we know how to handle. Type3 fonts
# are excluded -- they have no embedded font program, so a glyph-name
# or outline-fingerprint lookup has nothing to bind against.
_SIMPLE_FONT_TYPES = frozenset({"Type1", "MMType1", "TrueType"})

# Tibetan Machine / Chogyal CFF subsets name glyphs ``MT<byte>`` where <byte> is
# the font's native single-byte code (what the conversion tables key on), not an
# Adobe-Glyph-List name. Used by the from-scratch Encoding route below.
_MT_GLYPH_NAME = re.compile(r"^MT(\d+)$")


def _load_pdf_embedded_font(buf: bytes) -> Optional[TTFont]:
    """Load a PDF-embedded font as ``fontTools.TTFont``.

    Handles two cases:

    * SFNT-wrapped (OpenType / TrueType): direct ``TTFont`` load.
    * Bare CFF (what PyMuPDF returns for Type1 / Type1C fonts) -- wrap
      the CFF bytes in a minimal OTF on the fly so ``TTFont`` can read
      it. Only the tables :func:`fingerprint_glyph` needs (``CFF``,
      ``hmtx``, ``hhea``, ``maxp``, ``cmap``, ``head``, ``post``,
      ``name``, ``OS/2``) are emitted, with widths taken from the CFF
      CharStrings.
    """
    try:
        return TTFont(io.BytesIO(buf), lazy=False)
    except Exception:
        pass
    # Fallback: treat as raw CFF.
    try:
        return _wrap_cff_as_otf(buf)
    except Exception:
        return None


def _wrap_cff_as_otf(cff_bytes: bytes) -> TTFont:
    """Minimal in-memory CFF -> OpenType wrapper. Just enough to make
    :func:`pdf_cmap_fix.glyph_fingerprint.fingerprint_glyph` work
    (glyph names + outlines + advance widths)."""

    from fontTools.cffLib import CFFFontSet
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    cff = CFFFontSet()
    cff.decompile(io.BytesIO(cff_bytes), None, isCFF2=False)
    top = cff[0]

    glyph_order = list(top.getGlyphOrder())
    if ".notdef" in glyph_order:
        glyph_order = [".notdef"] + [g for g in glyph_order if g != ".notdef"]
    else:
        glyph_order = [".notdef"] + glyph_order

    upem = 1000
    private = getattr(top, "Private", None)
    default_w = int(round(getattr(private, "defaultWidthX", 0) or 0))

    widths: dict[str, int] = {}
    for gn in glyph_order:
        if gn == ".notdef":
            widths[gn] = upem
            continue
        try:
            cs = top.CharStrings[gn]
            # Force CharString decompilation so cs.width is populated.
            cs.draw(RecordingPen())
            w = getattr(cs, "width", None)
            widths[gn] = int(round(w)) if w is not None else default_w
        except Exception:
            widths[gn] = default_w

    tt = TTFont(sfntVersion="OTTO")
    tt.setGlyphOrder(glyph_order)

    # CFF table -- inject the parsed CFFFontSet directly.
    cff_table = newTable("CFF ")
    cff_table.cff = cff
    tt["CFF "] = cff_table

    head = newTable("head")
    head.tableVersion = 1.0
    head.fontRevision = 1.0
    head.checkSumAdjustment = 0
    head.magicNumber = 0x5F0F3CF5
    head.flags = 3
    head.unitsPerEm = upem
    head.created = head.modified = 0
    head.xMin = head.yMin = -1000
    head.xMax = head.yMax = 2000
    head.macStyle = 0
    head.lowestRecPPEM = 8
    head.fontDirectionHint = 2
    head.indexToLocFormat = 0
    head.glyphDataFormat = 0
    tt["head"] = head

    hhea = newTable("hhea")
    hhea.tableVersion = 0x00010000
    hhea.ascent = int(upem * 0.85)
    hhea.descent = -int(upem * 0.15)
    hhea.lineGap = 0
    hhea.advanceWidthMax = max(widths.values()) if widths else upem
    hhea.minLeftSideBearing = 0
    hhea.minRightSideBearing = 0
    hhea.xMaxExtent = hhea.advanceWidthMax
    hhea.caretSlopeRise = 1
    hhea.caretSlopeRun = 0
    hhea.caretOffset = 0
    hhea.reserved0 = hhea.reserved1 = hhea.reserved2 = hhea.reserved3 = 0
    hhea.metricDataFormat = 0
    hhea.numberOfHMetrics = len(glyph_order)
    tt["hhea"] = hhea

    maxp = newTable("maxp")
    maxp.tableVersion = 0x00005000  # CFF flavour
    maxp.numGlyphs = len(glyph_order)
    tt["maxp"] = maxp

    hmtx = newTable("hmtx")
    hmtx.metrics = {gn: (widths.get(gn, default_w), 0) for gn in glyph_order}
    tt["hmtx"] = hmtx

    post = newTable("post")
    post.formatType = 3.0  # no glyph names in the post table -- CFF carries them
    post.italicAngle = 0
    post.underlinePosition = -100
    post.underlineThickness = 50
    post.isFixedPitch = 0
    post.minMemType42 = post.maxMemType42 = post.minMemType1 = post.maxMemType1 = 0
    tt["post"] = post

    name = newTable("name")
    name.names = []
    tt["name"] = name

    os2 = newTable("OS/2")
    os2.version = 4
    os2.xAvgCharWidth = 500
    os2.usWeightClass = 400
    os2.usWidthClass = 5
    os2.fsType = 0
    os2.ySubscriptXSize = os2.ySubscriptYSize = 0
    os2.ySubscriptXOffset = os2.ySubscriptYOffset = 0
    os2.ySuperscriptXSize = os2.ySuperscriptYSize = 0
    os2.ySuperscriptXOffset = os2.ySuperscriptYOffset = 0
    os2.yStrikeoutSize = 50
    os2.yStrikeoutPosition = 250
    os2.sFamilyClass = 0
    panose = type("p", (), {})()
    for fld in (
        "bFamilyType", "bSerifStyle", "bWeight", "bProportion", "bContrast",
        "bStrokeVariation", "bArmStyle", "bLetterForm", "bMidline", "bXHeight",
    ):
        setattr(panose, fld, 0)
    os2.panose = panose
    os2.ulUnicodeRange1 = os2.ulUnicodeRange2 = 0
    os2.ulUnicodeRange3 = os2.ulUnicodeRange4 = 0
    os2.achVendID = "    "
    os2.fsSelection = 0x40
    os2.usFirstCharIndex = 0x20
    os2.usLastCharIndex = 0xFFFF
    os2.sTypoAscender = hhea.ascent
    os2.sTypoDescender = hhea.descent
    os2.sTypoLineGap = 0
    os2.usWinAscent = hhea.ascent
    os2.usWinDescent = -hhea.descent
    os2.ulCodePageRange1 = os2.ulCodePageRange2 = 0
    os2.sxHeight = int(upem * 0.5)
    os2.sCapHeight = int(upem * 0.7)
    os2.usDefaultChar = 0
    os2.usBreakChar = 0x20
    os2.usMaxContext = 0
    tt["OS/2"] = os2

    cmap = newTable("cmap")
    cmap.tableVersion = 0
    sub = CmapSubtable.newSubtable(4)
    sub.platformID = 3
    sub.platEncID = 1
    sub.format = 4
    sub.language = 0
    sub.cmap = {}
    cmap.tables = [sub]
    tt["cmap"] = cmap

    return tt


def _resolve_db_code_map_simple(
    doc: fitz.Document,
    font_xref: int,
    lookup_kind: str,
    inner: dict[str, str],
) -> dict[int, str]:
    """Resolve a simple-encoding (Type1 / MMType1 / TrueType) font to a
    ``{char_code (0..255) -> Unicode}`` mapping.

    Unlike :func:`_resolve_db_gid_map`, the keys here are **PDF char
    codes**, not GIDs, because in a simple font the content stream
    references glyphs by 1-byte char codes that go through
    ``/Encoding`` (predefined name + ``/Differences``) to land on a
    PostScript glyph name. Once we know the glyph name we can use the
    same gname / gshape lookups Type0 paths already consume.

    Tier 1 (``gid``) is intentionally not supported for simple fonts:
    GIDs in Type1 CharStrings are font-local and not portable between
    PDFs, so a single tier-1 entry could not be reused across
    documents.
    """

    if lookup_kind == "gid":
        # GID-based tier 1 is Type0-only by design (see docstring).
        return {}

    code_to_gname = resolve_simple_encoding(doc, font_xref)
    if not code_to_gname:
        return {}

    if lookup_kind == "gname":
        out: dict[int, str] = {}
        for code, gname in code_to_gname.items():
            u = inner.get(gname)
            if u:
                out[code] = u
        return out

    # lookup_kind == "gshape"
    try:
        tup = doc.extract_font(font_xref)
    except Exception:
        return {}
    if not tup or len(tup) < 4:
        return {}
    buf = tup[3]
    if not buf or not isinstance(buf, (bytes, bytearray)):
        return {}

    ext_font = _load_pdf_embedded_font(bytes(buf))
    if ext_font is None:
        return {}
    try:
        from pdf_cmap_fix.glyph_fingerprint import fingerprint_glyph

        out2: dict[int, str] = {}
        # Cache fingerprints per glyph name so we hash each glyph at
        # most once even when several codes alias to the same name
        # (extremely rare but allowed by the spec).
        fp_cache: dict[str, Optional[str]] = {}
        for code, gname in code_to_gname.items():
            if gname not in fp_cache:
                fp_cache[gname] = fingerprint_glyph(ext_font, gname)
            fp = fp_cache[gname]
            if not fp:
                continue
            u = inner.get(fp)
            if u:
                out2[code] = u
        return out2
    finally:
        try:
            ext_font.close()
        except Exception:
            pass


def _extract_font_names(doc: fitz.Document, font_xref: int) -> list[str]:
    """Return candidate font name strings from an embedded font's name table.

    Reads nameIDs 6 (PostScript), 4 (Full Name), and 1 (Family Name) from the
    OpenType/TrueType name table of the font embedded at ``font_xref``. These
    are fed to :func:`_pick_best_font_key` *before* the PDF-level ``BaseFont``
    name so that generic wrapper names such as ``CIDFont+F1`` do not prevent
    the correct lookup file (e.g. ``microsofthimalaya.json``) from being found.

    Returns an empty list if the font cannot be extracted or parsed, so callers
    fall back to the PDF basename without raising.
    """
    try:
        tup = doc.extract_font(font_xref)
    except Exception:
        return []
    if not tup or len(tup) < 4:
        return []
    buf = tup[3]
    if not buf or not isinstance(buf, (bytes, bytearray)):
        return []
    ext_font: Optional[TTFont] = None
    try:
        ext_font = TTFont(io.BytesIO(bytes(buf)), lazy=False)
        name_table = ext_font.get("name")
        if name_table is None:
            return []
        candidates: list[str] = []
        for name_id in (6, 4, 1):
            for record in name_table.names:
                if record.nameID != name_id:
                    continue
                try:
                    s = record.toUnicode()
                except Exception:
                    try:
                        s = record.string.decode("latin-1", errors="replace")
                    except Exception:
                        continue
                s = s.strip()
                if s and s not in candidates:
                    candidates.append(s)
        return candidates
    except Exception:
        return []
    finally:
        if ext_font is not None:
            try:
                ext_font.close()
            except Exception:
                pass


_LOOKUP_FILE_CACHE: dict[tuple[str, float, int], Optional[Tuple[str, dict[str, str]]]] = {}

# --- name-index (font-ID) matching -----------------------------------------
#
# The tier-1 (gid) lookup directory built by
# ``scripts/gid/rebuild_indexed_lookup.py`` is keyed by an opaque content-hash
# font ID (``<id>.json``) and ships a ``_name_index.json`` that maps normalised
# font names to those IDs, split by name-table role. When that index is present
# we match PDF fonts against real name-table names (PostScript primary, then
# full / family / filename) instead of fuzzy-matching against filenames. This
# is both more accurate (``TimesNewRomanPSMT`` no longer substring-hits the
# ``Ma``/Mantra font) and version-aware (``TibetanChogyalUnicode-141208``
# resolves to the matching 2014 face, not a different release ``…-170221``).

_NAME_INDEX_FILENAME = "_name_index.json"
_INDEX_ROLES = ("ps", "full", "family", "filename")
_DIGITS = "0123456789"
# Prefix matches (one normalised name is a prefix of the other, e.g. a style
# suffix like ``Regular``) only count when the shorter name is reasonably long
# and covers a good fraction of the longer -- this keeps short unrelated names
# from latching onto long PDF names.
_PREFIX_MIN_LEN = 5
_PREFIX_MIN_RATIO = 0.5

_NAME_INDEX_CACHE: dict[tuple[str, float, int], Optional[dict]] = {}


def _load_name_index(lookup_dir: Path) -> Optional[dict]:
    path = lookup_dir / _NAME_INDEX_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _load_name_index_cached(lookup_dir: Path) -> Optional[dict]:
    path = lookup_dir / _NAME_INDEX_FILENAME
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), st.st_mtime, st.st_size)
    if key in _NAME_INDEX_CACHE:
        return _NAME_INDEX_CACHE[key]
    loaded = _load_name_index(lookup_dir)
    _NAME_INDEX_CACHE[key] = loaded
    return loaded


def _resolve_index_font_ids(
    index: dict, pdf_norm_candidates: list[str]
) -> Tuple[Optional[str], list[str]]:
    """Resolve normalised PDF font names to candidate font IDs via the index.

    ``pdf_norm_candidates`` is an ordered list (best first, e.g. embedded
    PostScript name then PDF basename) of names already passed through
    :func:`_normalise_name`. Returns ``(matched_name, [font_id, ...])`` for the
    single best-scoring match, or ``(None, [])`` if nothing matched. Several
    IDs are returned when one name legitimately maps to multiple faces (e.g.
    two releases sharing a PostScript name); the caller breaks that tie using
    referenced-GID coverage.

    Match tiers, strongest first:

    1. exact name equality;
    2. version/date-suffix equality -- the PDF name with trailing digits
       stripped equals an index name (``tibetanchogyalunicode141208`` ->
       ``tibetanchogyalunicode``); never matches a *different* numeric suffix;
    3. prefix relationship (style suffix like ``Regular``), gated by length.
    """
    results: list[tuple[tuple[int, int, int, int], str, tuple[str, ...]]] = []
    for ci, q in enumerate(pdf_norm_candidates):
        if not q:
            continue
        qs = q.rstrip(_DIGITS)
        for ri, role in enumerate(_INDEX_ROLES):
            names = index.get(role)
            if not isinstance(names, dict) or not names:
                continue
            exact_ids = names.get(q)
            if exact_ids:
                results.append(((3, len(q), -ci, -ri), q, tuple(exact_ids)))
            if qs and qs != q:
                vs_ids = names.get(qs)
                if vs_ids:
                    results.append(((2, len(qs), -ci, -ri), qs, tuple(vs_ids)))
            if len(q) >= _PREFIX_MIN_LEN:
                for nm, nids in names.items():
                    # Only the "DB name is a prefix of the PDF name" direction
                    # is allowed (the PDF appended a style suffix like
                    # ``Regular``). The reverse -- the PDF name being a prefix
                    # of a longer DB name -- is a *different* suffix
                    # (``times`` -> ``timescsx``, ``timesnewroman`` ->
                    # ``timesnewromandia``) and is exactly the kind of loose
                    # match we must reject.
                    if len(nm) < _PREFIX_MIN_LEN or len(nm) >= len(q):
                        continue
                    if q.startswith(nm) and len(nm) / len(q) >= _PREFIX_MIN_RATIO:
                        results.append(((1, len(nm), -ci, -ri), nm, tuple(nids)))
    if not results:
        return None, []
    results.sort(key=lambda r: r[0], reverse=True)
    top_score = results[0][0]
    matched_name = results[0][1]
    ids: list[str] = []
    for score, _name, rids in results:
        if score != top_score:
            break
        for fid in rids:
            if fid not in ids:
                ids.append(fid)
    return matched_name, ids


def _compute_db_map(
    doc: fitz.Document,
    font_xref: int,
    is_type0: bool,
    match_kind: str,
    inner: dict[str, str],
) -> dict[int, str]:
    """Build the ``{code -> Unicode}`` map for one font from a loaded lookup.

    Dispatches on font family and lookup tier exactly like the historical
    inline logic: Type0 + ``gid`` uses the raw GID map, Type0 + ``gname`` /
    ``gshape`` resolves through the embedded font, and simple fonts go through
    the char-code path.
    """
    if is_type0:
        if match_kind == "gid":
            return _gid_map_from_inner(inner)
        return _resolve_db_gid_map(doc, font_xref, match_kind, inner)
    return _resolve_db_code_map_simple(doc, font_xref, match_kind, inner)


def _pick_db_map_via_index(
    doc: fitz.Document,
    font_xref: int,
    *,
    is_type0: bool,
    tier: str,
    lookup_dir: Path,
    name_index: dict,
    pdf_norm_candidates: list[str],
    referenced: set,
) -> Tuple[Optional[dict[int, str]], Optional[str], str, Optional[str]]:
    """Index-based match. Returns ``(db_map, font_id, match_kind, matched_name)``.

    Among the font IDs a name resolves to, the one whose lookup covers the most
    referenced GIDs wins (ties broken by larger overall coverage), so a richer
    face (e.g. the 1036-glyph release) beats a sparse one sharing the same name.
    """
    matched_name, cand_ids = _resolve_index_font_ids(name_index, pdf_norm_candidates)
    if not cand_ids:
        return None, None, "", None
    best: Optional[tuple[tuple[int, int], str, str, dict[int, str]]] = None
    for cid in cand_ids:
        path = lookup_dir / f"{cid}.json"
        if not path.is_file():
            continue
        loaded = _load_lookup_file_cached(path)
        if loaded is None or loaded[0] != tier:
            continue
        match_kind, inner = loaded
        cand_map = _compute_db_map(doc, font_xref, is_type0, match_kind, inner)
        if not cand_map:
            continue
        overlap = len(cand_map.keys() & referenced) if referenced else 0
        score = (overlap, len(cand_map))
        if best is None or score > best[0]:
            best = (score, cid, match_kind, cand_map)
    if best is None:
        return None, None, "", matched_name
    _score, font_id, match_kind, db_map = best
    return db_map, font_id, match_kind, matched_name


def _load_lookup_file_cached(path: Path) -> Optional[Tuple[str, dict[str, str]]]:
    """Process-global cached version of :func:`_load_lookup_file`.

    Lookup JSON files are read-only data shipped with the package (or
    pointed at via ``--font-lookup-dir``); parsing
    ``microsofthimalaya.json`` (~1.5 MB, 1500 entries) once per
    ``patch_doc`` call dominates batch runtimes.
    """
    try:
        st = path.stat()
    except OSError:
        return _load_lookup_file(path)
    cache_key = (str(path.resolve()), st.st_mtime, st.st_size)
    if cache_key in _LOOKUP_FILE_CACHE:
        return _LOOKUP_FILE_CACHE[cache_key]
    loaded = _load_lookup_file(path)
    _LOOKUP_FILE_CACHE[cache_key] = loaded
    return loaded


def _hex_to_unicode(hex_str: str) -> str:
    """Decode an even-length hex string as a UTF-16-BE unicode value."""
    if not hex_str:
        return ""
    if len(hex_str) % 2 == 1:
        hex_str = "0" + hex_str
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return ""
    if len(raw) % 2 == 1:
        raw = b"\x00" + raw
    try:
        return raw.decode("utf-16-be", errors="replace")
    except UnicodeDecodeError:
        return ""


def _tokenise_cmap_block(text: str):
    """Tokenise a bfchar/bfrange block.

    Yields ``("hex", value)`` for ``<...>`` tokens, ``("[", None)``,
    ``("]", None)`` for array delimiters, and ignores everything else.
    """
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "<":
            j = text.find(">", i)
            if j == -1:
                break
            yield ("hex", text[i + 1 : j])
            i = j + 1
            continue
        if c == "[":
            yield ("[", None)
            i += 1
            continue
        if c == "]":
            yield ("]", None)
            i += 1
            continue
        i += 1


def _parse_tounicode(stream: bytes) -> dict:
    """Parse a CMap ToUnicode stream.

    Handles the three forms in PDF 1.7 §9.10.3 / Adobe TN 5099:
    ``bfchar``, range-form ``bfrange`` and array-form ``bfrange``.
    The previous regex-based implementation silently mis-parsed the
    array form (``<lo> <hi> [<u1> <u2> …]``), inflating clean CMaps to
    millions of bogus entries (see bug 02-performance.md).
    """
    text = stream.decode("latin-1", errors="replace")
    result: dict[int, str] = {}

    for blk in re.finditer(r"beginbfchar(.*?)endbfchar", text, re.DOTALL):
        toks = list(_tokenise_cmap_block(blk.group(1)))
        i = 0
        while i + 1 < len(toks):
            ka, va = toks[i]
            kb, vb = toks[i + 1]
            if ka == "hex" and kb == "hex":
                try:
                    code = int(va, 16)
                except ValueError:
                    i += 2
                    continue
                result[code] = _hex_to_unicode(vb)
                i += 2
            else:
                i += 1

    for blk in re.finditer(r"beginbfrange(.*?)endbfrange", text, re.DOTALL):
        toks = list(_tokenise_cmap_block(blk.group(1)))
        i = 0
        while i < len(toks):
            if i + 1 >= len(toks):
                break
            t_lo = toks[i]
            t_hi = toks[i + 1]
            if t_lo[0] != "hex" or t_hi[0] != "hex":
                i += 1
                continue
            try:
                lo = int(t_lo[1], 16)
                hi = int(t_hi[1], 16)
            except ValueError:
                i += 2
                continue
            j = i + 2
            if j >= len(toks):
                break
            if toks[j][0] == "hex":
                base_hex = toks[j][1]
                base_uni = _hex_to_unicode(base_hex)
                if base_uni:
                    span = hi - lo + 1
                    if span > 0 and span <= 0x10000:
                        for off in range(span):
                            if len(base_uni) == 1:
                                result[lo + off] = chr(
                                    (ord(base_uni[0]) + off) & 0xFFFF
                                )
                            else:
                                last = ord(base_uni[-1])
                                result[lo + off] = (
                                    base_uni[:-1]
                                    + chr((last + off) & 0xFFFF)
                                )
                i = j + 1
            elif toks[j][0] == "[":
                k = j + 1
                idx = 0
                while k < len(toks) and toks[k][0] != "]":
                    if toks[k][0] == "hex":
                        if idx <= hi - lo:
                            result[lo + idx] = _hex_to_unicode(toks[k][1])
                        idx += 1
                    k += 1
                i = k + 1 if k < len(toks) else k
            else:
                i = j

    return result


def _build_tounicode_type0(mapping: dict) -> bytes:
    entries = [
        f"<{gid:04X}> <{''.join(f'{ord(c):04X}' for c in uni)}>"
        for gid, uni in sorted(mapping.items())
    ]
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
        f"{len(entries)} beginbfchar",
        *entries,
        "endbfchar",
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return "\n".join(lines).encode("latin-1")


def _build_tounicode_simple(mapping: dict) -> bytes:
    """ToUnicode CMap for simple (Type1 / TrueType) fonts.

    Differences from :func:`_build_tounicode_type0`:

    * Codespace is ``<00> <FF>`` (single byte) -- matches the 1-byte
      char codes simple fonts use in their content streams.
    * Keys are emitted as 2-hex-digit codes.

    Any mapping key outside ``0..255`` is silently dropped: simple
    fonts cannot reference it from the content stream anyway.
    """

    entries = [
        f"<{cc:02X}> <{''.join(f'{ord(c):04X}' for c in uni)}>"
        for cc, uni in sorted(mapping.items())
        if 0 <= cc <= 0xFF
    ]
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
        f"{len(entries)} beginbfchar",
        *entries,
        "endbfchar",
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return "\n".join(lines).encode("latin-1")


def _merge(
    existing: dict,
    db_map: dict,
    referenced: Optional[set] = None,
) -> tuple:
    """Merge ``db_map`` into ``existing``.

    If ``referenced`` is given, only GIDs in that set are eligible for
    upgrade. This is the key performance lever on subsetted fonts: a
    Word-style ``microsofthimalaya.json`` lookup has ~1500 entries, but
    a per-page subset typically references ~10–80 GIDs. Without this
    filter we'd treat the other ~1400 entries as ``changed`` and
    rewrite the (semantically identical) ToUnicode stream for every
    subset (see bug report 02-performance.md). When ``referenced`` is
    ``None`` (back-compat) we fall back to the old "consider every
    db_map entry" behaviour.
    """
    merged = dict(existing)
    changed = 0
    if referenced is None:
        items = db_map.items()
    else:
        items = ((gid, db_map[gid]) for gid in db_map.keys() & referenced)
    for gid, db_uni in items:
        if db_uni != existing.get(gid, ""):
            merged[gid] = db_uni
            changed += 1
    return merged, changed


def _is_tibetan_text(s: str) -> bool:
    """True when ``s`` is non-empty and entirely Tibetan script (U+0F00-0FFF).

    Used to tell an existing ToUnicode entry that already carries real Unicode
    from a legacy one, whose entries are the Latin-ish stand-ins an ANSI-era
    Tibetan font emits (``!`` for ``ཀ``, ...).
    """
    lo, hi = TIBETAN_RANGE
    return bool(s) and all(lo <= ord(c) <= hi for c in s)


def _has_tibetan(s: str) -> bool:
    lo, hi = TIBETAN_RANGE
    return any(lo <= ord(c) <= hi for c in s)


def _map_has_tibetan(db_map: dict) -> bool:
    """True when any value in a lookup map contains Tibetan script.

    The shipped ``jbhgzwhz`` GID dump is a cmap of a non-Unicode HuaGuang
    face: every value is CJK/PUA, none is ``U+0Fxx``. Applying it cannot
    recover Tibetan and will overwrite Distiller's GB-as-CJK ToUnicode
    with a different CJK soup.
    """
    return any(v and _has_tibetan(v) for v in db_map.values())


def _unicode_from_uni_glyph_name(gname: str) -> Optional[str]:
    """Decode an explicit ``uniXXXX`` / ``uXXXXX`` glyph name to Tibetan.

    ``fontTools.agl.toUnicode`` already concatenates ``uni`` 4-hex groups
    (``uni0F400F74`` → ``ཀུ``). We only accept those explicit Unicode names
    whose decoded value contains Tibetan, so Latin AGL names (``comma``,
    ``A``) and PUA ``uniF001`` leftovers cannot overwrite a good ToUnicode.
    """
    if not gname or not _UNI_GLYPH_NAME_RE.match(gname):
        return None
    from fontTools.agl import toUnicode

    decoded = toUnicode(gname)
    if decoded and _has_tibetan(decoded):
        return decoded
    return None


def _tounicode_from_embedded_uni_names(
    doc: fitz.Document,
    font_xref: int,
    existing: dict,
) -> Optional[dict[int, str]]:
    """Build a ToUnicode map from ``uni*`` glyph names on a simple font.

    Quartz / Affinity TrueType subsets often ship no ``/Encoding``, a cmap
    whose keys *are* the PDF char codes, and glyph names like ``uni0F400F74``,
    while the PDF ToUnicode maps those same codes to ASCII (``'``, ``,``,
    ``0``). The names are the source of truth.
    """
    encoding = resolve_simple_encoding(doc, font_xref)
    if not encoding:
        return None
    db_map: dict[int, str] = {}
    for code, gname in encoding.items():
        uni = _unicode_from_uni_glyph_name(gname)
        if not uni:
            continue
        old = existing.get(code, "")
        if _is_tibetan_text(old) and not (uni == old or uni.startswith(old)):
            continue
        db_map[code] = uni
    return db_map or None


def _huaguang_tounicode(
    doc: fitz.Document,
    xref: int,
    basename: str,
    *,
    is_type0: bool,
    existing: dict,
) -> Optional[tuple[dict[int, str], str]]:
    """Recover Tibetan for a HuaGuang / Founder plane font.

    Returns ``(db_map, "huaguang")`` or ``None`` when the face is not
    HuaGuang or the table cannot decode any slot.
    """
    from pdf_cmap_fix import huaguang as hg

    if not hg.is_huaguang_font(basename):
        return None
    if not is_type0:
        encoding = resolve_simple_encoding(doc, xref)
        plane_map = hg.tounicode_from_plane_encoding(basename, encoding)
        if plane_map:
            return plane_map, "huaguang"
    if existing:
        cjk_map = hg.tounicode_from_cjk_map(existing, basename)
        if cjk_map:
            return cjk_map, "huaguang"
    return None


def _gid_map_corroborated(
    existing: dict,
    db_map: dict,
    referenced: Optional[set] = None,
    min_conflicts: int = MIN_GID_CONFLICTS,
) -> bool:
    """True unless ``db_map`` looks keyed to a *different* glyph order.

    Tier-1 lookups key on raw GIDs, which only transfer when the embedded font
    program has the same glyph order as the font the DB was built from. Font
    names are reused across incompatible releases (``TibetanChogyalUnicode``
    ships a dozen dated builds; ``MonlamUniOuChan1`` several), and a name match
    alone cannot tell them apart -- so a wrong pick silently overwrites correct
    Unicode with values from an unrelated glyph space.

    The check looks only at codes where the PDF's existing ToUnicode already
    resolves to Tibetan, i.e. entries that are worth protecting:

    * ``db == existing`` or ``db.startswith(existing)`` **corroborates** the
      map. The prefix case is the tool's whole purpose -- the DB decomposing a
      stacked syllable the existing map only partially represented
      (``ས`` -> ``སྒྲུབ``).
    * anything else **conflicts**.

    A compatible map always produces corroboration (measured across the bundled
    examples: 5750 / 67 / 79 corroborations against 1423 / 13 / 18 conflicts).
    A map from a different glyph order produces *none at all*. So we reject only
    on zero corroboration plus at least ``min_conflicts`` conflicts, which keeps
    the check inert for legacy fonts (their existing entries are Latin and are
    skipped outright) and for thin evidence.
    """
    codes = (db_map.keys() & referenced) if referenced else db_map.keys()
    corroborated = conflicts = 0
    for code in codes:
        old = existing.get(code, "")
        if not _is_tibetan_text(old):
            continue
        new = db_map[code]
        if new == old or new.startswith(old):
            corroborated += 1
        else:
            conflicts += 1
    return not (corroborated == 0 and conflicts >= min_conflicts)


def _overrides(existing: dict, merged: dict) -> dict:
    out = {}
    for k, v in merged.items():
        if existing.get(k, "") != v:
            out[k] = v
    return out


def _load_embedded_ttfont(
    doc: fitz.Document, font_xref: int
) -> Optional[TTFont]:
    """Load the embedded font program for ``font_xref`` as a ``TTFont``."""
    try:
        tup = doc.extract_font(font_xref)
    except Exception:
        return None
    if not tup or len(tup) < 4:
        return None
    buf = tup[3]
    if not buf or not isinstance(buf, (bytes, bytearray)):
        return None
    try:
        return TTFont(io.BytesIO(bytes(buf)), lazy=False)
    except Exception:
        return None


def _build_ptg_db_map(
    name: str, existing: dict[int, str], match_kind: str
) -> Optional[tuple[dict[int, str], str, str, str]]:
    """Re-map each code's existing ToUnicode value through table ``name``.

    Works for both simple fonts (1-byte codes) and Type0/Identity-H fonts
    (2-byte codes == GIDs): in either case the PDF's existing ToUnicode maps the
    code to a (wrong) Latin-ish character, and re-mapping that character through
    the curated per-font table recovers the real Unicode. Robust to a malformed
    codespace (e.g. 4-hex ``<0003>`` keys) since :func:`_parse_tounicode` already
    normalises the key width.
    """
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    db_map: dict[int, str] = {}
    for code, value in existing.items():
        converted = ptg.convert_text(name, value)
        if converted:
            db_map[code] = converted
    if not db_map:
        return None
    return db_map, name, match_kind, name


def _pytiblegenc_name_map(
    embedded_names: list[str],
    basename: str,
    existing: dict[int, str],
) -> Optional[tuple[dict[int, str], str, str, str]]:
    """Cheap name-based pytiblegenc match (no embedded-font I/O).

    Resolves the font name (embedded name-table names first, then the PDF
    basename) to a vendored conversion table and re-maps the existing ToUnicode.
    """
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    for cand in [*embedded_names, basename]:
        name, table = ptg.table_for(cand)
        if table is not None:
            return _build_ptg_db_map(name, existing, "ptg")
    return None


def _pytiblegenc_outline_map(
    doc: fitz.Document,
    xref: int,
    existing: dict[int, str],
) -> Optional[tuple[dict[int, str], str, str, str]]:
    """Expensive last-resort match: identify a renamed/obfuscated legacy font by
    its embedded glyph outlines, then re-map the existing ToUnicode.

    Loads and parses the embedded font program, so only call this once the
    cheaper name-based paths (pytiblegenc + the regular lookup index) have all
    failed.
    """
    from pdf_cmap_fix import pytiblegenc_tables as ptg
    from pdf_cmap_fix.glyph_outline_id import identify_candidates

    ttfont = _load_embedded_ttfont(doc, xref)
    if ttfont is None:
        return None
    candidates = identify_candidates(ttfont)
    if not candidates:
        return None
    name, table = ptg.table_for_candidates(candidates)
    if name is None:
        return None
    return _build_ptg_db_map(name, existing, "ptg-outline")


def _recover_codes_from_outlines(
    ttfont: TTFont,
    name: str,
    *,
    is_type0: bool,
    referenced: Optional[set],
) -> dict[int, str]:
    """Recover ``{code: tibetan}`` by hashing each code's glyph outline.

    Used for symbolic TrueType subsets that ship no usable /Encoding or glyph
    names: hash the glyph each code draws and look the hash up in pytiblegenc's
    glyph DB to recover the ``(font, codepoint)`` it stands for, then convert
    that code point through the per-font table.

    The code set is the embedded subset's own cmap (simple fonts) or the
    content-stream-referenced GIDs (Type0/Identity-H, where the code *is* the
    GID); a subset only embeds the glyphs it uses, so either is complete.
    """
    from pdf_cmap_fix import pytiblegenc_tables as ptg
    from pdf_cmap_fix.glyph_outline_id import compute_glyph_hash, glyph_lookup_tables

    if "glyf" not in ttfont:
        return {}

    glyph_order = ttfont.getGlyphOrder()
    if is_type0:
        # Identity-H: the code is the GID.
        codes = {c for c in (referenced or set()) if 0 <= c < len(glyph_order)}

        def glyph_name_for(code: int) -> Optional[str]:
            return glyph_order[code]
    else:
        # Symbolic subsets carry a (1,0) or (3,0) cmap subtable mapping the
        # 1-byte code to a glyph name (a (3,0) symbol cmap keys on 0xF000+code).
        if "cmap" not in ttfont:
            return {}
        codemap: dict[int, str] = {}
        try:
            for sub in ttfont["cmap"].tables:
                for code, gname in sub.cmap.items():
                    codemap.setdefault(code, gname)
        except Exception:
            return {}
        if not codemap:
            return {}
        codes = set(codemap)

        def glyph_name_for(code: int) -> Optional[str]:
            g = codemap.get(code)
            if g is None and 0 <= code <= 0xFF:
                g = codemap.get(0xF000 + code)
            return g

    # The glyph DB keys siblings on the *raw* PostScript name (e.g. ``Dedris-a``)
    # plus its own code point; the conversion tables key on the *normalised* name
    # (``Ededris-a``). Normalise a sibling's name so its table can be resolved.
    def _norm(fc: tuple) -> Optional[str]:
        n, _t = ptg.table_for(fc[0])
        return n

    _forward, reverse = glyph_lookup_tables()
    db_map: dict[int, str] = {}
    for code in codes:
        gname = glyph_name_for(code)
        if not gname:
            continue
        glyph_hash = compute_glyph_hash(ttfont, gname)
        if not glyph_hash:
            continue
        sibs = reverse.get(glyph_hash)
        if not sibs:
            continue
        # Prefer the identified font, then take the first sibling whose table
        # actually converts the code point (mirrors _glyph_lookup_recover).
        ordered = sorted(sibs, key=lambda fc: (_norm(fc) != name, fc))
        for sib_font, cp in ordered:
            norm_sib = _norm((sib_font, cp))
            if not norm_sib:
                continue
            converted = ptg.convert_char(norm_sib, chr(cp))
            if converted:
                db_map[code] = converted
                break
    return db_map


def _recover_codes_from_shapes(
    doc: fitz.Document,
    xref: int,
    *,
    is_type0: bool,
    referenced: Optional[set],
    font_hint: Optional[str] = None,
) -> tuple[Optional[str], dict[int, str]]:
    """Recover ``(font_name, {code: tibetan})`` by matching glyph *shapes*.

    The representation-independent counterpart to
    :func:`_recover_codes_from_outlines`: it reads the embedded program as a
    CFF-capable font (so Type1/CFF works, unlike the glyf-only hash path),
    fingerprints each code's glyph outline, votes a reference font, and recovers
    each code's native byte. Returns ``(None, {})`` when numpy/the shape DB is
    unavailable or nothing matches confidently.
    """
    from pdf_cmap_fix import glyph_shape_id
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    if not glyph_shape_id.available():
        return None, {}
    try:
        tup = doc.extract_font(xref)
    except Exception:
        return None, {}
    if not tup or len(tup) < 4 or not tup[3]:
        return None, {}
    ttfont = _load_pdf_embedded_font(bytes(tup[3]))
    if ttfont is None:
        return None, {}
    try:
        glyph_set = ttfont.getGlyphSet()
    except Exception:
        return None, {}
    # unitsPerEm: prefer the CFF FontMatrix (what the DB build used), else head.
    upem = None
    if "CFF " in ttfont:
        try:
            cff = ttfont["CFF "].cff
            fm = cff[cff.fontNames[0]].rawDict.get("FontMatrix")
            if fm and fm[0]:
                upem = round(1 / fm[0])
        except Exception:
            upem = None
    if not upem:
        upem = ttfont["head"].unitsPerEm if "head" in ttfont else 1000

    # code -> embedded glyph name to draw
    pairs: list[tuple[int, str]] = []
    if is_type0:
        order = ttfont.getGlyphOrder()
        for c in (referenced or set()):
            if 0 <= c < len(order):
                pairs.append((c, order[c]))
    else:
        enc = resolve_simple_encoding(doc, xref)
        pairs = list(enc.items())
    if not pairs:
        return None, {}

    font_name, code_to_byte = glyph_shape_id.identify_and_recover(
        glyph_set, upem, pairs, font_hint=font_hint
    )
    if not font_name or not code_to_byte:
        return None, {}
    db_map: dict[int, str] = {}
    for code, bcode in code_to_byte.items():
        conv = ptg.convert_char(font_name, chr(bcode))
        if conv:
            db_map[code] = conv
    if not db_map:
        return None, {}
    return font_name, db_map


def _legacy_tounicode_from_scratch(
    doc: fitz.Document,
    xref: int,
    basename: str,
    *,
    is_type0: bool,
    referenced: Optional[set],
) -> Optional[tuple[dict[int, str], str]]:
    """Build a ToUnicode map *from scratch* for a legacy Tibetan font that ships
    no usable ToUnicode.

    The remap paths above all need an existing (wrong, Latin-ish) ToUnicode to
    re-map, but many legacy non-Unicode Tibetan PDFs (Ededris/Dedris,
    TibetanChogyal, ...) carry none. We reconstruct the per-code character the
    legacy table expects by one of two routes and convert it through the table:

    * **Encoding** -- a simple font's ``/Encoding`` maps each code to a glyph
      name whose Adobe-Glyph-List code point *is* that Latin-ish character (this
      is exactly what a ToUnicode would have carried). Covers the common
      Type1/CFF and named-TrueType case.
    * **Outlines** -- symbolic subsets strip /Encoding and glyph names, so fall
      back to hashing each code's glyph outline (see
      :func:`_recover_codes_from_outlines`). Also the Type0/Identity-H path.

    The conversion table is resolved from the PDF base font name first, then --
    for fonts embedded under an obfuscated name -- from the glyph outlines
    (exact TrueType hashes, or fuzzy CFF shape matching). TrueType fonts whose
    hashes are absent from the glyph DB are *not* run through shape matching:
    that path false-positives Latin faces (Optima, Lucida Grande) as
    ``Ededris-vowa`` and then remaps their MacRoman encoding to Tibetan.

    Returns ``(db_map, matched_font_name)`` or ``None`` when the font is not a
    recoverable legacy face.
    """
    from pdf_cmap_fix import huaguang as hg
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    # HuaGuang faces are GB-encoded, not Ededris/Chogyal. Shape matching
    # votes them as Ededris-a / TibetanChogyal and remaps Lxx / CJK slots
    # into stacked-Sanskrit soup. The dedicated HuaGuang path runs first.
    if hg.is_huaguang_font(basename):
        return None

    # Resolve the per-font conversion table: by name first, else by hashing the
    # embedded outlines (handles obfuscated PostScript names). Non-legacy faces
    # resolve to neither and bail out.
    name, _table = ptg.table_for(basename)
    ttfont = _load_embedded_ttfont(doc, xref)
    # Shape-recovery result, computed at most once and reused for code recovery.
    shape_font: Optional[str] = None
    shape_map: Optional[dict[int, str]] = None
    if name is None:
        # TrueType glyf outlines -> exact hash identification.
        if ttfont is not None and "glyf" in ttfont:
            from pdf_cmap_fix.glyph_outline_id import identify_candidates

            name, _table = ptg.table_for_candidates(identify_candidates(ttfont))
        # CFF/Type1 (no glyf hashes to compare) -> shape matching, which also
        # recovers an obfuscated name from the glyph outlines. Do not use this
        # as a fallback for TrueType: a glyf font that failed exact-hash ID is
        # not a vendored legacy face, and the bitmap matcher false-positives
        # large Latin subsets as Ededris-vowa (see tests for Optima / Lucida).
        if name is None:
            is_truetype_glyf = ttfont is not None and "glyf" in ttfont and "CFF " not in ttfont
            if not is_truetype_glyf:
                shape_font, shape_map = _recover_codes_from_shapes(
                    doc, xref, is_type0=is_type0, referenced=referenced
                )
                name = shape_font
        if name is None:
            return None

    db_map: dict[int, str] = {}

    # Encoding route (simple fonts only -- Type0 uses a CMap, not /Encoding).
    if not is_type0:
        encoding = resolve_simple_encoding(doc, xref)
        if encoding:
            from fontTools.agl import toUnicode

            for code, gname in encoding.items():
                ch = toUnicode(gname)
                if not ch:
                    # Some legacy CFF/Type1 subsets (Tibetan Machine / Chogyal
                    # family) name glyphs ``MT<byte>`` where <byte> IS the font's
                    # native code that the conversion table is keyed on -- not an
                    # AGL name. Recover that byte directly.
                    m = _MT_GLYPH_NAME.match(gname)
                    if m:
                        cp = int(m.group(1))
                        if 0 <= cp <= 0x10FFFF:
                            ch = chr(cp)
                if not ch:
                    continue
                converted = ptg.convert_char(name, ch)
                if converted:
                    db_map[code] = converted

    # Outline route: symbolic subsets with no usable /Encoding, and Type0 fonts.
    if not db_map and ttfont is not None:
        db_map = _recover_codes_from_outlines(
            ttfont, name, is_type0=is_type0, referenced=referenced
        )

    # Shape route: CFF/Type1 outlines the glyf hash path above cannot read.
    # Reuse the map already computed during identification when possible.
    if not db_map:
        if shape_map and shape_font == name:
            db_map = shape_map
        else:
            sf, smap = _recover_codes_from_shapes(
                doc, xref, is_type0=is_type0, referenced=referenced, font_hint=name
            )
            if smap:
                db_map = smap

    if not db_map:
        return None
    return db_map, name


def collect_font_merges(
    doc: fitz.Document,
    *,
    lookup_dir: Path,
    tier: LookupTier,
    verbose: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Scan PDF fonts with a ``/ToUnicode`` entry, compute merged maps
    against the lookup directory, and return them as records (without
    rewriting the PDF).

    Two font-type paths are handled:

    * **Type0 (composite, Identity-H)** -- the historical path. PDF
      char codes ARE GIDs; the merge happens on
      ``{GID -> Unicode}`` and the output ToUnicode stream uses a
      4-hex-digit codespace.
    * **Type1, MMType1, TrueType (simple, single-byte)** -- new path,
      enabled for ``gname`` and ``gshape`` lookup tiers only (tier 1
      ``gid`` keys are GID-based and not portable across simple-font
      subsets). PDF char codes go through the font's
      ``/Encoding`` (predefined base + ``/Differences``) to a
      PostScript glyph name, which is what the lookup keys on. The
      merge happens on ``{char_code -> Unicode}`` and the output
      ToUnicode stream uses a 2-hex-digit codespace.

    Type3 (procedural) fonts have no embedded font program and are
    silently skipped.

    Only JSON whose ``_meta.lookup_kind`` matches ``tier`` is used
    (``gid`` tier also accepts files with missing ``lookup_kind``).
    """
    keys_disk = _discover_lookup_keys(lookup_dir)
    db_index = _build_db_index(keys_disk)
    # When the lookup dir ships a name index (font-ID-keyed tier-1 DB) we match
    # on real name-table names; otherwise we fall back to legacy filename-stem
    # fuzzy matching (still used by the gname / gshape tiers).
    name_index = _load_name_index_cached(lookup_dir)

    stats = dict(fonts_seen=0, patched=0, upgrades=0, no_change=0, no_match=0)
    records: list[dict[str, Any]] = []
    seen: set = set()
    reported: set = set()

    # Pre-scan: split font xrefs by family. We need both groups up
    # front so the content-stream walk can decode each font's strings
    # at the correct byte width (2 bytes for Type0/Identity-H,
    # 1 byte for simple fonts).
    type0_xrefs: set[int] = set()
    simple_xrefs: set[int] = set()
    for pno in range(len(doc)):
        for f in doc[pno].get_fonts(full=True):
            xref, _, ftype, _, _, _, _ = f
            if ftype == "Type0":
                type0_xrefs.add(xref)
            elif ftype in _SIMPLE_FONT_TYPES:
                simple_xrefs.add(xref)
    referenced_by_xref = collect_referenced_gids(
        doc,
        type0_xrefs=type0_xrefs,
        simple_xrefs=simple_xrefs,
    )

    for pno in range(len(doc)):
        for f in doc[pno].get_fonts(full=True):
            xref, _, ftype, basename, _, _, _ = f
            if xref in seen:
                continue
            seen.add(xref)

            is_type0 = ftype == "Type0"
            is_simple = ftype in _SIMPLE_FONT_TYPES
            if not (is_type0 or is_simple):
                # Type3 (no font program) or unknown types.
                continue
            stats["fonts_seen"] += 1

            font_obj = doc.xref_object(xref)
            m = re.search(r"/ToUnicode (\d+) 0 R", font_obj)
            tu_xref = int(m.group(1)) if m else None
            existing: dict = {}
            if tu_xref is not None:
                try:
                    existing = _parse_tounicode(doc.xref_stream(tu_xref))
                except Exception:
                    existing = {}

            # The "referenced" set (content-stream-derived codes/GIDs) is the
            # performance lever on subsets *and* the tie-breaker when a name
            # maps to several faces (see bug 02-performance.md). It is also the
            # only key set available when synthesising a ToUnicode below.
            referenced = referenced_by_xref.get(xref)

            # HuaGuang (华光) / Founder plane fonts: 2-byte GB-style slots, not
            # the Ededris/Chogyal single-byte tables. Handle them before GID
            # name matching (the shipped jbhgzwhz dump has no Tibetan) and
            # before CFF shape matching (which votes these faces as Ededris).
            hg_built = _huaguang_tounicode(
                doc, xref, basename, is_type0=is_type0, existing=existing
            )
            if hg_built is not None:
                db_map, matched_name = hg_built
                if not existing:
                    if verbose:
                        norm = _normalise_name(basename)
                        if norm not in reported:
                            reported.add(norm)
                            print(
                                f"  [created] {basename[:50]} -> {matched_name}  "
                                f"[huaguang]  ({ftype})"
                            )
                    records.append(
                        {
                            "font_xref": xref,
                            "to_unicode_xref": None,
                            "pdf_font_name": basename,
                            "pdf_font_type": ftype,
                            "db_key_matched": matched_name,
                            "db_name_matched": matched_name,
                            "existing": {},
                            "merged": db_map,
                            "overrides": dict(db_map),
                            "changed": len(db_map),
                        }
                    )
                    stats["patched"] += 1
                    stats["upgrades"] += len(db_map)
                    continue
                if not referenced:
                    referenced = set(existing.keys())
                merged, changed = _merge(existing, db_map, referenced)
                overrides = _overrides(existing, merged)
                if verbose:
                    norm = _normalise_name(basename)
                    if norm not in reported:
                        reported.add(norm)
                        print(
                            f"  [matched] {basename[:50]} -> {matched_name}  "
                            f"[huaguang]  ({ftype})"
                        )
                records.append(
                    {
                        "font_xref": xref,
                        "to_unicode_xref": tu_xref,
                        "pdf_font_name": basename,
                        "pdf_font_type": ftype,
                        "db_key_matched": matched_name,
                        "db_name_matched": matched_name,
                        "existing": existing,
                        "merged": merged,
                        "overrides": overrides,
                        "changed": changed,
                    }
                )
                if changed:
                    stats["patched"] += 1
                    stats["upgrades"] += changed
                else:
                    stats["no_change"] += 1
                continue

            # A legacy Tibetan font (Ededris/Dedris, TibetanChogyal, ...)
            # frequently ships *no* ToUnicode at all, so the remap pipeline below
            # has nothing to work with and the font would be silently skipped.
            # Reconstruct the per-code Unicode -- from the /Encoding glyph names
            # or, for symbolic subsets, the glyph outlines -- and synthesise a
            # fresh ToUnicode CMap (created and attached in the apply phase).
            # Non-legacy faces resolve to no table and bail out in the helper.
            if not existing:
                built = _legacy_tounicode_from_scratch(
                    doc, xref, basename, is_type0=is_type0, referenced=referenced
                )
                if built is None:
                    stats["no_change"] += 1
                    continue
                db_map, matched_name = built
                norm = _normalise_name(basename)
                if verbose and norm not in reported:
                    reported.add(norm)
                    print(
                        f"  [created] {basename[:50]} -> {matched_name}  "
                        f"[ptg-from-scratch]  ({ftype})"
                    )
                records.append(
                    {
                        "font_xref": xref,
                        "to_unicode_xref": None,  # created + attached in apply
                        "pdf_font_name": basename,
                        "pdf_font_type": ftype,
                        "db_key_matched": matched_name,
                        "db_name_matched": matched_name,
                        "existing": {},
                        "merged": db_map,
                        "overrides": dict(db_map),
                        "changed": len(db_map),
                    }
                )
                stats["patched"] += 1
                stats["upgrades"] += len(db_map)
                continue

            # Falling back to the existing ToUnicode keys is a safe
            # approximation for Word-style subsets when content-stream parsing
            # came up empty.
            if not referenced:
                referenced = set(existing.keys())

            # Build the ordered list of candidate names: the embedded font's
            # own name-table names first (PostScript / full / family) so that
            # generic PDF aliases like CIDFont+F1 / T1_0 / F0 do not drive the
            # match, then the PDF basename.
            embedded_names = _extract_font_names(doc, xref)
            match_kind = ""
            db_map = None
            db_key = None
            matched_display: Optional[str] = None

            # Legacy non-Unicode Tibetan fonts (Ededris/Dedris, TibetanChogyal,
            # ...) carry no usable GSUB/cmap, so the regular lookups produce
            # garbage. Their existing ToUnicode already yields a (wrong)
            # Latin-ish char per code; re-mapping that char through pytiblegenc's
            # curated per-font table recovers the real Unicode. This applies to
            # both simple and Type0/Identity-H faces (a Type0 legacy font with a
            # 2-byte/4-hex ToUnicode would otherwise be mis-matched by the GID
            # tier to a sparse same-named lookup). Cheap name-based resolution
            # only; the outline fallback (which loads the embedded program) runs
            # below as a last resort.
            from pdf_cmap_fix import huaguang as hg

            ptg_eligible = (
                (is_simple or is_type0)
                and bool(existing)
                and not hg.is_huaguang_font(basename)
            )
            if ptg_eligible:
                ptg_map = _pytiblegenc_name_map(embedded_names, basename, existing)
                if ptg_map is not None:
                    db_map, db_key, match_kind, matched_display = ptg_map

            if db_map is None and name_index is not None:
                pdf_norm_candidates: list[str] = []
                for cand in [*embedded_names, basename]:
                    q = _normalise_name(cand)
                    if q and q not in pdf_norm_candidates:
                        pdf_norm_candidates.append(q)
                db_map, db_key, match_kind, matched_display = _pick_db_map_via_index(
                    doc,
                    xref,
                    is_type0=is_type0,
                    tier=tier,
                    lookup_dir=lookup_dir,
                    name_index=name_index,
                    pdf_norm_candidates=pdf_norm_candidates,
                    referenced=referenced,
                )
            elif db_map is None:
                picked = None
                for cand in embedded_names:
                    picked = _pick_best_font_key(db_index, cand)
                    if picked:
                        break
                if picked is None:
                    picked = _pick_best_font_key(db_index, basename)
                if picked is not None:
                    path = lookup_dir / f"{picked}.json"
                    if path.is_file():
                        loaded = _load_lookup_file_cached(path)
                        if loaded is not None and loaded[0] == tier:
                            match_kind, inner = loaded
                            cand_map = _compute_db_map(
                                doc, xref, is_type0, match_kind, inner
                            )
                            if cand_map:
                                db_map = cand_map
                                db_key = picked
                                matched_display = picked
            if not db_map:
                db_map, db_key = None, None

            # Last resort for an unmatched legacy font with an obfuscated name:
            # identify it from its embedded glyph outlines. Deferred to here (and
            # gated on a no-match) because it loads/parses the embedded font
            # program, which must not run for every well-named font.
            if db_map is None and ptg_eligible:
                ptg_map = _pytiblegenc_outline_map(doc, xref, existing)
                if ptg_map is not None:
                    db_map, db_key, match_kind, matched_display = ptg_map

            # A tier-1 map is keyed on raw GIDs and was picked on the strength
            # of a font *name* alone. When the embedded program turns out to
            # use a different glyph order (a same-named but incompatible
            # release), applying it would replace correct Unicode with values
            # from an unrelated glyph space -- so drop it and keep what the PDF
            # already has. Only the gid tier needs this: gname / gshape resolve
            # through the embedded font, and ptg only fires on legacy fonts.
            rejected_gid_map = False
            if db_map is not None and match_kind == "gid" and not _map_has_tibetan(db_map):
                db_map, db_key, matched_display = None, None, None
                rejected_gid_map = True
            if (
                db_map is not None
                and match_kind == "gid"
                and not _gid_map_corroborated(existing, db_map, referenced)
            ):
                db_map, db_key, matched_display = None, None, None
                rejected_gid_map = True

            # Simple TrueType with no /Encoding and no lookup hit: decode
            # explicit uni* glyph names from the embedded cmap. Quartz /
            # Affinity subsets keep those names while writing ASCII ToUnicode
            # for stacked syllables.
            if db_map is None and is_simple:
                uni_map = _tounicode_from_embedded_uni_names(doc, xref, existing)
                if uni_map is not None:
                    db_map = uni_map
                    db_key = "embedded-uni-names"
                    match_kind = "uni-name"
                    matched_display = "embedded-uni-names"

            if db_map is None:
                stats["no_match"] += 1
                norm = _normalise_name(basename)
                if verbose and norm not in reported:
                    reported.add(norm)
                    if rejected_gid_map:
                        print(
                            f"  [rejected] {basename[:50]}  "
                            f"[gid map contradicts existing ToUnicode]  ({ftype})"
                        )
                    else:
                        print(f"  [no DB match] {basename}")
                merged = dict(existing)
                changed = 0
                overrides = {}
            else:
                norm = _normalise_name(basename)
                if verbose and norm not in reported:
                    reported.add(norm)
                    label = matched_display or db_key
                    print(
                        f"  [matched] {basename[:50]} -> {label}  "
                        f"[{match_kind}]  ({ftype})"
                    )
                # Word emits content-stream sentinel GIDs that may be absent
                # from the referenced set; fold the ones the DB knows about in
                # so they still get patched (see Word ActualText handling).
                if is_type0:
                    referenced = set(referenced) | (
                        db_map.keys() & WORD_SENTINEL_GIDS
                    )
                merged, changed = _merge(existing, db_map, referenced)
                overrides = _overrides(existing, merged)

            records.append(
                {
                    "font_xref": xref,
                    "to_unicode_xref": tu_xref,
                    "pdf_font_name": basename,
                    "pdf_font_type": ftype,
                    "db_key_matched": db_key,
                    "db_name_matched": matched_display,
                    "existing": existing,
                    "merged": merged,
                    "overrides": overrides,
                    "changed": changed,
                }
            )

            if db_map is None:
                pass
            elif changed == 0:
                stats["no_change"] += 1
            else:
                stats["patched"] += 1
                stats["upgrades"] += changed

    return records, stats


def apply_font_merges_to_doc(doc: fitz.Document, records: list[dict[str, Any]]) -> None:
    """Write merged ToUnicode streams for records with changed > 0.

    The CMap codespace size depends on the source font type:
    Type0 uses 2-byte codes (``<XXXX>``), every simple font type
    (Type1, MMType1, TrueType) uses 1-byte codes (``<XX>``). Records
    written by older versions of :func:`collect_font_merges` that
    don't carry ``pdf_font_type`` are treated as Type0 for backward
    compatibility.
    """
    for r in records:
        if r["changed"] <= 0:
            continue
        ftype = r.get("pdf_font_type", "Type0")
        if ftype in _SIMPLE_FONT_TYPES:
            cmap_bytes = _build_tounicode_simple(r["merged"])
        else:
            cmap_bytes = _build_tounicode_type0(r["merged"])
        tu_xref = r.get("to_unicode_xref")
        if tu_xref:
            # Repair/enhance an existing ToUnicode stream in place.
            doc.update_stream(tu_xref, cmap_bytes)
        else:
            # Legacy font that shipped no ToUnicode: create a new stream object
            # and attach it to the font dict's /ToUnicode entry.
            new_xref = doc.get_new_xref()
            doc.update_object(new_xref, "<<>>")
            doc.update_stream(new_xref, cmap_bytes)
            doc.xref_set_key(r["font_xref"], "ToUnicode", f"{new_xref} 0 R")


def _strip_word_actualtext_sentinels_in_stream(
    stream: bytes,
) -> tuple[bytes, int]:
    """Remove Word ``ActualText<FEFFFFFD>`` wrappers from one content stream.

    Keeps the enclosed content (e.g. ``<0384>Tj``) and strips only the
    marked-content wrapper so extraction does not surface U+FFFD sentinels.
    """
    if not stream:
        return stream, 0
    out, n = _WORD_ACTUALTEXT_SENTINEL_RE.subn(lambda m: m.group(1), stream)
    return out, n


def strip_word_actualtext_sentinels(doc: fitz.Document) -> dict[str, int]:
    """Strip Word FEFF/FFFD ActualText wrappers across page content streams."""
    seen_stream_xrefs: set[int] = set()
    markers_removed = 0
    streams_changed = 0
    for pno in range(len(doc)):
        page = doc[pno]
        try:
            content_xrefs = page.get_contents()
        except Exception:
            continue
        for xref in content_xrefs:
            if xref in seen_stream_xrefs:
                continue
            seen_stream_xrefs.add(xref)
            try:
                stream = doc.xref_stream(xref)
            except Exception:
                continue
            new_stream, n = _strip_word_actualtext_sentinels_in_stream(stream)
            if n <= 0:
                continue
            try:
                doc.update_stream(xref, new_stream)
            except Exception:
                continue
            markers_removed += n
            streams_changed += 1
    return dict(
        actualtext_removed=markers_removed,
        actualtext_streams_changed=streams_changed,
    )


def patch_doc(
    doc: fitz.Document,
    *,
    lookup_dir: Path,
    tier: LookupTier,
    verbose: bool = False,
) -> dict[str, int]:
    records, stats = collect_font_merges(
        doc,
        lookup_dir=lookup_dir,
        tier=tier,
        verbose=verbose,
    )
    apply_font_merges_to_doc(doc, records)
    clean_stats = strip_word_actualtext_sentinels(doc)
    stats = dict(stats)
    stats.update(clean_stats)
    return stats


def extract_all(doc: fitz.Document) -> str:
    pages = []
    for pno in range(len(doc)):
        text = doc[pno].get_text(
            "text",
            flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES,
        )
        pages.append(f"=== PAGE {pno+1} ===\n{text.strip()}")
    return "\n".join(pages)


def build_tounicode_dict(
    pdf_path,
    *,
    lookup_dir: Path,
    tier: LookupTier,
) -> dict[str, Any]:
    pdf_path = Path(pdf_path)

    doc = fitz.open(str(pdf_path))
    try:
        records, stats = collect_font_merges(
            doc,
            lookup_dir=lookup_dir,
            tier=tier,
            verbose=False,
        )
    finally:
        doc.close()

    by_xref = {str(r["font_xref"]): r for r in records}
    return {"fonts": records, "by_font_xref": by_xref, "stats": stats}


def patch_pdf(
    pdf_path,
    output_path=None,
    write_file: bool = True,
    *,
    lookup_dir: Path,
    tier: LookupTier,
    verbose: bool = False,
) -> dict:
    pdf_path = Path(pdf_path)
    stem = pdf_path.stem

    out_path: Optional[Path] = None
    if write_file:
        out_path = Path(output_path) if output_path else pdf_path.parent / f"{stem}.patched.pdf"

    doc = fitz.open(str(pdf_path))
    try:
        stats = patch_doc(
            doc,
            lookup_dir=lookup_dir,
            tier=tier,
            verbose=verbose,
        )
        pdf_bytes = doc.tobytes(**_PDF_SAVE_KW)
    finally:
        doc.close()

    if write_file and out_path is not None:
        out_path.write_bytes(pdf_bytes)

    return dict(pdf_bytes=pdf_bytes, stats=stats, output_path=out_path)


def extract_pdf_text(
    pdf_path,
    output_dir=None,
    write_files: bool = True,
    *,
    lookup_dir: Path,
    tier: LookupTier,
    verbose: bool = False,
) -> dict:
    pdf_path = Path(pdf_path)
    out_dir = Path(output_dir) if output_dir else pdf_path.parent
    stem = pdf_path.stem

    doc_raw = fitz.open(str(pdf_path))
    raw_text = extract_all(doc_raw)
    doc_raw.close()

    doc_pat = fitz.open(str(pdf_path))
    try:
        stats = patch_doc(
            doc_pat,
            lookup_dir=lookup_dir,
            tier=tier,
            verbose=verbose,
        )
        patched_text = extract_all(doc_pat)
    finally:
        doc_pat.close()

    raw_lines = raw_text.splitlines()
    pat_lines = patched_text.splitlines()
    diff_lines = [
        (i, r, p)
        for i, (r, p) in enumerate(zip(raw_lines, pat_lines))
        if r != p
    ]
    char_delta = len(patched_text) - len(raw_text)

    if write_files:
        (out_dir / f"{stem}.raw.txt").write_text(raw_text, encoding="utf-8")
        (out_dir / f"{stem}.patched.txt").write_text(patched_text, encoding="utf-8")
        with open(out_dir / f"{stem}.diff.txt", "w", encoding="utf-8") as df:
            df.write(
                f"PDF:           {pdf_path.name}\n"
                f"Lines changed: {len(diff_lines)}\n"
                f"Char delta:    {char_delta:+d}\n\n"
            )
            for i, r, p in diff_lines:
                df.write(f"--- line {i+1} RAW:\n{r}\n")
                df.write(f"+++ line {i+1} PATCHED:\n{p}\n\n")

    return dict(
        raw=raw_text,
        patched=patched_text,
        stats=stats,
        diff_lines=diff_lines,
        char_delta=char_delta,
    )


def _serialise_cmap_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert int-keyed inner dicts to str for JSON."""

    def cmap_dict(d: dict) -> dict[str, str]:
        return {str(k): v for k, v in sorted(d.items())}

    out_fonts = []
    for r in payload["fonts"]:
        out_fonts.append(
            {
                "font_xref": r["font_xref"],
                "to_unicode_xref": r["to_unicode_xref"],
                "pdf_font_name": r["pdf_font_name"],
                "db_key_matched": r["db_key_matched"],
                "existing": cmap_dict(r["existing"]),
                "merged": cmap_dict(r["merged"]),
                "overrides": cmap_dict(r["overrides"]),
                "changed": r["changed"],
            }
        )
    return {"fonts": out_fonts, "stats": payload["stats"]}


def _sanitise_json_utf8(obj: Any) -> Any:
    """Replace lone UTF-16 surrogates so ``json`` output can be written as UTF-8."""

    def _fix_str(s: str) -> str:
        return "".join("\ufffd" if 0xD800 <= ord(c) <= 0xDFFF else c for c in s)

    if isinstance(obj, str):
        return _fix_str(obj)
    if isinstance(obj, dict):
        return {_fix_str(k) if isinstance(k, str) else k: _sanitise_json_utf8(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise_json_utf8(x) for x in obj]
    return obj


def _printable(s: str) -> str:
    return "".join(c if c >= " " else f"[{ord(c):02X}]" for c in s)


def _show_preview(label: str, text: str, n: int = PREVIEW_LINES) -> None:
    print(f"\n  --- {label} (first {n} non-empty lines) ---")
    count = 0
    for line in text.splitlines():
        if line.strip() and not line.startswith("=== PAGE"):
            print(f"    {_printable(line)}")
            count += 1
            if count >= n:
                break


def _show_diff_sample(raw: str, patched: str, n: int = PREVIEW_DIFF) -> None:
    raw_lines = raw.splitlines()
    pat_lines = patched.splitlines()
    diffs = [(i, r, p) for i, (r, p) in enumerate(zip(raw_lines, pat_lines)) if r != p]
    print(f"\n  --- Sample of changed lines ({min(n, len(diffs))} of {len(diffs)}) ---")
    for i, r, p in diffs[:n]:
        print(f"    line {i+1}:")
        print(f"      RAW:     {_printable(r)}")
        print(f"      PATCHED: {_printable(p)}")


def _data_root_from_lookup(default_lookup_dir: Path) -> Path:
    """Return the package data root for a default lookup directory."""
    if default_lookup_dir.name == "font_lookup_byid":
        return default_lookup_dir.parent
    return default_lookup_dir.parent


def _default_strategy_specs(default_lookup_dir: Path) -> dict[str, StrategySpec]:
    """Strategies exposed by the top-level CLI.

    The tier-specific CLIs continue to use their historical fixed tier. The
    top-level command can try the same bundled lookup trees users would
    otherwise invoke manually.
    """
    data_root = _data_root_from_lookup(default_lookup_dir)
    return {
        "gid": ("gid", default_lookup_dir),
        "gid-pua-free": ("gid", data_root / "font_lookup_gid_pua_free"),
        "gname": ("gname", data_root / "font_lookup_gname"),
        "gname-pua-free": ("gname", data_root / "font_lookup_gname_pua_free"),
        "gshape": ("gshape", data_root / "font_lookup_gshape"),
        "gshape-pua-free": ("gshape", data_root / "font_lookup_gshape_pua_free"),
    }


def _text_quality(text: str) -> dict[str, int]:
    tibetan = 0
    non_tibetan_non_ascii = 0
    for c in text:
        cp = ord(c)
        if TIBETAN_RANGE[0] <= cp <= TIBETAN_RANGE[1]:
            tibetan += 1
        elif cp > 0x7F:
            non_tibetan_non_ascii += 1
    return {
        "tibetan": tibetan,
        "non_tibetan_non_ascii": non_tibetan_non_ascii,
        "chars": len(text),
    }


def _quality_sort_key(quality: dict[str, int]) -> tuple[int, int, int]:
    return (
        quality["tibetan"],
        -quality["non_tibetan_non_ascii"],
        quality["chars"],
    )


def _select_auto_strategy(
    pdf_path: Path,
    *,
    strategies: dict[str, StrategySpec],
    verbose: bool = True,
) -> tuple[str, LookupTier, Path, Optional[dict]]:
    """Pick the strategy whose patched extraction has the most Tibetan text."""
    best: Optional[tuple[tuple[int, int, int], str, LookupTier, Path, dict, dict[str, int]]] = None
    if verbose:
        print("\n[Auto strategy] Scoring bundled lookup strategies ...")
    for name, (cand_tier, cand_lookup) in strategies.items():
        if not cand_lookup.is_dir():
            if verbose:
                print(f"  {name:16s} skipped (missing lookup dir: {cand_lookup})")
            continue
        result = extract_pdf_text(
            pdf_path,
            write_files=False,
            lookup_dir=cand_lookup,
            tier=cand_tier,
            verbose=False,
        )
        quality = _text_quality(result["patched"])
        key = _quality_sort_key(quality)
        stats = result["stats"]
        if verbose:
            print(
                f"  {name:16s} tibetan={quality['tibetan']} "
                f"non_tib_non_ascii={quality['non_tibetan_non_ascii']} "
                f"upgrades={stats['upgrades']}"
            )
        if best is None or key > best[0]:
            best = (key, name, cand_tier, cand_lookup, result, quality)

    if best is None:
        raise RuntimeError("auto strategy could not find any usable lookup directory")

    _, name, selected_tier, selected_lookup, result, quality = best
    if verbose:
        print(
            f"  selected:      {name} "
            f"({selected_tier}, {selected_lookup})"
        )
        if name != "gid":
            print(
                "  warning: auto selected a non-default lookup strategy; "
                "review extraction output if this PDF mixes unusual fonts."
            )
    return name, selected_tier, selected_lookup, result


def cli_main(
    argv: list[str],
    *,
    tier: LookupTier,
    default_lookup_dir: Path,
    usage_text: str,
    program_label: str,
    strategy_specs: Optional[dict[str, StrategySpec]] = None,
) -> None:
    """Shared argv parser and driver for tier CLIs."""
    import io as io_mod

    if hasattr(sys.stdout, "buffer") and not isinstance(
        sys.stdout, io_mod.TextIOWrapper
    ):
        sys.stdout = io_mod.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )

    if not argv:
        sys.exit(usage_text)
    if argv[0] in ("-h", "--help"):
        sys.stdout.write(usage_text)
        sys.exit(0)

    font_lookup_cli: Optional[Path] = None
    strategy_cli: Optional[str] = "auto" if strategy_specs is not None else None
    i = 0
    while i < len(argv):
        if argv[i] == "--font-lookup-dir" and i + 1 < len(argv):
            font_lookup_cli = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--strategy" and i + 1 < len(argv):
            strategy_cli = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--no-auto":
            strategy_cli = "gid"
            i += 1
            continue
        if argv[i] == "--pua-free":
            strategy_cli = "gid-pua-free"
            i += 1
            continue
        if argv[i] == "--gshape":
            strategy_cli = "gshape"
            i += 1
            continue
        if argv[i] == "--gshape-pua-free":
            strategy_cli = "gshape-pua-free"
            i += 1
            continue
        break
    argv = argv[i:]

    lookup_root = default_lookup_dir.resolve()
    selected_tier = tier
    selected_strategy = strategy_cli or tier
    auto_enabled = strategy_specs is not None and strategy_cli == "auto"

    if font_lookup_cli is not None:
        lookup_root = font_lookup_cli.expanduser().resolve()
        selected_strategy = "custom"
        auto_enabled = False
    elif strategy_specs is not None:
        if strategy_cli == "auto":
            lookup_root = default_lookup_dir.resolve()
        elif strategy_cli in strategy_specs:
            selected_tier, lookup_root = strategy_specs[strategy_cli]
            lookup_root = lookup_root.resolve()
        else:
            choices = ", ".join(["auto", *strategy_specs])
            sys.exit(f"unknown --strategy {strategy_cli!r}; choices: {choices}")

    if not auto_enabled and not lookup_root.is_dir():
        flag = "--font-lookup-dir" if font_lookup_cli is not None else "lookup dir"
        sys.exit(f"font lookup directory not found ({flag}): {lookup_root}")

    dump_cmap: Optional[str] = None
    patch_pdf_mode = False
    rest = list(argv)
    if rest and rest[0] in ("--patch-pdf", "-p"):
        patch_pdf_mode = True
        rest = rest[1:]
    elif rest and rest[0] == "--dump-cmap":
        if len(rest) < 2:
            sys.exit(f"{program_label} --dump-cmap requires OUTPUT.json and at least one PDF")
        dump_cmap = rest[1]
        rest = rest[2:]

    pdf_args = rest
    if not pdf_args:
        sys.exit(usage_text)

    if auto_enabled:
        print("Loading font lookup strategies (auto) ...")
        for name, (_, cand_lookup) in strategy_specs.items():
            if cand_lookup.is_dir():
                n_lookup = len(_discover_lookup_keys(cand_lookup))
                print(f"  {name:16s} {n_lookup} font JSON files under {cand_lookup}")
            else:
                print(f"  {name:16s} missing lookup dir: {cand_lookup}")
    else:
        n_lookup = len(_discover_lookup_keys(lookup_root))
        print(f"Loading font lookup ({selected_tier}) ...")
        print(f"  strategy: {selected_strategy}")
        print(f"  {n_lookup} font JSON files under {lookup_root}")

    for arg in pdf_args:
        pdf_path = Path(arg)
        if not pdf_path.exists():
            print(f"  SKIP (not found): {arg}", file=sys.stderr)
            continue

        stem = pdf_path.stem
        print(f"\n{'='*65}")
        print(f"  PDF: {pdf_path.name}")
        print(f"{'='*65}")

        result_for_text: Optional[dict] = None
        run_tier = selected_tier
        run_lookup = lookup_root
        run_strategy = selected_strategy
        if auto_enabled:
            try:
                run_strategy, run_tier, run_lookup, result_for_text = _select_auto_strategy(
                    pdf_path,
                    strategies=strategy_specs,
                    verbose=True,
                )
            except RuntimeError as e:
                print(f"  SKIP ({e})", file=sys.stderr)
                continue

        if dump_cmap is not None:
            out_json = Path(dump_cmap)
            if len(pdf_args) > 1:
                out_json = out_json.parent / f"{out_json.stem}_{pdf_path.stem}{out_json.suffix}"
            payload = build_tounicode_dict(
                pdf_path,
                lookup_dir=run_lookup,
                tier=run_tier,
            )
            serial = _sanitise_json_utf8(_serialise_cmap_result(payload))
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(serial, ensure_ascii=False, indent=2), encoding="utf-8")
            s = payload["stats"]
            print(f"  fonts seen:    {s['fonts_seen']}")
            print(f"  would patch:   {s['patched']}  ({s['upgrades']} GID upgrades)")
            print(f"  no change:     {s['no_change']}")
            print(f"  no DB match:   {s['no_match']}")
            if s.get("actualtext_removed", 0) > 0:
                print(
                    "  stripped:      "
                    f"{s['actualtext_removed']} Word ActualText marker(s)"
                )
            print(f"  Written: {out_json}")
            continue

        if patch_pdf_mode:
            print("\n[Patch-only mode] Rewriting ToUnicode CMaps ...")
            print(f"  strategy:      {run_strategy}")
            result = patch_pdf(
                pdf_path,
                lookup_dir=run_lookup,
                tier=run_tier,
                verbose=True,
            )
            stats = result["stats"]
            pdf_bytes = result["pdf_bytes"]
            out_path = result["output_path"]
            print(f"  fonts seen:    {stats['fonts_seen']}")
            print(f"  patched:       {stats['patched']}  ({stats['upgrades']} GID upgrades)")
            print(f"  no change:     {stats['no_change']}")
            print(f"  no DB match:   {stats['no_match']}")
            if stats.get("actualtext_removed", 0) > 0:
                print(
                    "  stripped:      "
                    f"{stats['actualtext_removed']} Word ActualText marker(s)"
                )
            print(f"  Written: {out_path}  ({len(pdf_bytes):,} bytes)")
            continue

        print("\n[Phase 1] Raw extraction ...")
        if result_for_text is None:
            result = extract_pdf_text(
                pdf_path,
                lookup_dir=run_lookup,
                tier=run_tier,
                verbose=True,
            )
        else:
            print(f"  strategy:      {run_strategy}")
            result = result_for_text
        raw_text = result["raw"]
        patched_text = result["patched"]
        stats = result["stats"]
        diff_lines = result["diff_lines"]
        char_delta = result["char_delta"]
        pages = len(raw_text.split("=== PAGE "))
        print(f"  {len(raw_text):,} chars, {pages-1} pages")
        _show_preview("RAW TEXT", raw_text)

        print("\n[Phase 2] Patched result ...")
        print(f"  fonts seen:    {stats['fonts_seen']}")
        print(f"  patched:       {stats['patched']}  ({stats['upgrades']} GID upgrades)")
        print(f"  no change:     {stats['no_change']}")
        print(f"  no DB match:   {stats['no_match']}")
        if stats.get("actualtext_removed", 0) > 0:
            print(
                "  stripped:      "
                f"{stats['actualtext_removed']} Word ActualText marker(s)"
            )
        print(f"  Written: {pdf_path.parent}/{stem}.{{raw,patched,diff}}.txt")
        _show_preview("PATCHED TEXT", patched_text)
        print("\n[Diff]")
        print(
            f"  Lines changed: {len(diff_lines)} / "
            f"{max(len(raw_text.splitlines()), len(patched_text.splitlines()))}"
        )
        print(f"  Char delta:    {char_delta:+d}")
        _show_diff_sample(raw_text, patched_text)

    print("\nDone.")
