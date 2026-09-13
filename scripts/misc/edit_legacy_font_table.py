#!/usr/bin/env python3
"""Build a throwaway HTML UI to compare and edit a legacy Tibetan font table.

Keeps this script; the generated UI is local output (default:
``scripts/misc/out/table-ui/``) and is gitignored via ``scripts/misc/out/``.

Example (Sheja / TB-Youtso)::

    PYTHONPATH=. python scripts/misc/edit_legacy_font_table.py \\
        --pdf 'bugs3/1 sheja-jan-01.pdf' 'bugs3/3 sheja-mar-01.pdf' \\
        --font-substr Youtso \\
        --tables /home/eroux/BUDA/softs/pydeduff/pytiblegenc/font-tables \\
        --edits bugs3/youtso_tables.csv \\
        --serve
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from collections import defaultdict
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "scripts" / "misc" / "out" / "table-ui"
DEFAULT_TABLES = Path("/home/eroux/BUDA/softs/pydeduff/pytiblegenc/font-tables")
DEFAULT_JSEWTS = Path("/home/eroux/BUDA/softs/jsewts/src/jsewts.js")

# Visual truth for the Sheja January 2001 masthead (page 0).
# ``zÙh-Mæ-æÙ-`` draws as བོད་རྒྱལ་ལོ་ in TB-Youtso / TBYTNTT.
SHEJA_HINTS = {
    "z": "བ",
    "Ù": "ོ",
    "h": "ད",
    "-": "་",
    "M": "རྒྱ",
    "æ": "ལ",
}

# Visual truth for Dzongkha / Tibetan Calligraphic (precomposed stacks).
CALLIGRAPHIC_HINTS = {
    "!": "ཀ",
    "D": "རྟ",
    "e": "བྱ",
    "i": "གྲ",
    "v": "བླ",
    "\u00a6": "ུ",
}


def _load_tables(tables_dir: Path) -> dict[str, dict[str, dict[int, str]]]:
    """``{source_stem: {font_name: {cp: unicode}}}``."""
    out: dict[str, dict[str, dict[int, str]]] = {}
    if not tables_dir.is_dir():
        return out
    skip = {"glyph_db.csv"}
    for path in sorted(tables_dir.glob("*.csv")):
        if path.name in skip:
            continue
        by_font: dict[str, dict[int, str]] = defaultdict(dict)
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh, quotechar='"'):
                if len(row) < 3:
                    continue
                font, cp_s, value = row[0], row[1], row[2]
                try:
                    cp = int(cp_s)
                except ValueError:
                    continue
                by_font[font][cp] = value
        out[path.stem] = dict(by_font)
    return out


def _matching_fonts(tables: dict[str, dict[str, dict[int, str]]], substr: str) -> list[str]:
    names: set[str] = set()
    needle = substr.lower()
    for by_font in tables.values():
        for name in by_font:
            if needle in name.lower():
                names.add(name)
    return sorted(names)


def _open_pdf(path: Path):
    import fitz

    return fitz.open(str(path))


def _tounicode_for_font(doc, xref: int) -> dict[int, str]:
    from pdf_cmap_fix.tounicode_core import _parse_tounicode as parse_cmap

    obj = doc.xref_object(xref)
    m = re.search(r"/ToUnicode (\d+) 0 R", obj)
    if not m:
        return {}
    try:
        stream = doc.xref_stream(int(m.group(1)))
    except Exception:
        return {}
    return parse_cmap(stream) if stream else {}


def _font_tail(name: str) -> str:
    return name.split("+", 1)[-1]


def _is_latin_companion(pdf_font_name: str) -> bool:
    """TB1-Youtso is a Latin face shipped beside the Tibetan TB / TB2 planes."""
    tail = _font_tail(pdf_font_name)
    return tail.startswith("TB1-") or tail.startswith("TB1")


def _table_font_name(pdf_font_name: str) -> str:
    from pdf_cmap_fix.pytiblegenc_tables import normalize_font_name

    return normalize_font_name(_font_tail(pdf_font_name))


def _charstring_names(buf: bytes) -> set[str]:
    """Glyph names actually present in an embedded CFF/Type1 program."""
    if not buf:
        return set()
    try:
        cs = getattr(_cff_top(buf), "CharStrings", None)
    except Exception:
        return set()
    return set(cs.keys()) if cs else set()


def _is_sfnt(buf: bytes) -> bool:
    return bool(buf) and buf[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")


def _is_type1_pfa(buf: bytes) -> bool:
    return bool(buf) and buf.startswith((b"%!FontType1", b"%!PS-AdobeFont"))


_TYPE1_ENC_DUP = re.compile(r"dup\s+(\d+)\s+/([A-Za-z0-9._]+)\s+put")


def _type1_builtin_names(buf: bytes) -> dict[int, str]:
    """Type1 built-in Encoding (``dup 65 /G41 put``) from the PFA header.

    Distiller MSTT faces name every slot ``Gxx`` and ship no Unicode cmap, so
    CFF/TTF outline drawing and ``insert_text`` both miss them.
    """
    if not _is_type1_pfa(buf):
        return {}
    end = buf.find(b"currentfile eexec")
    header = buf[: end if end >= 0 else min(len(buf), 20_000)].decode(
        "latin-1", errors="ignore"
    )
    out: dict[int, str] = {}
    for m in _TYPE1_ENC_DUP.finditer(header):
        name = m.group(2)
        if name != ".notdef":
            out[int(m.group(1))] = name
    return out


def _embedded_name_index(buf: bytes) -> tuple[set[str], dict[int, str]]:
    """Names present in the embedding, plus cmap / Type1 code → name."""
    cff = _charstring_names(buf)
    cmap_names: dict[int, str] = {}
    ttf_names: set[str] = set()
    if _is_sfnt(buf):
        try:
            import io

            from fontTools.ttLib import TTFont

            tt = TTFont(io.BytesIO(buf))
            ttf_names = set(tt.getGlyphOrder()) - {".notdef"}
            if "cmap" in tt:
                for table in tt["cmap"].tables:
                    for code, gname in (getattr(table, "cmap", None) or {}).items():
                        if gname and gname != ".notdef" and gname in ttf_names:
                            cmap_names.setdefault(int(code), gname)
        except Exception:
            pass
    t1_names = _type1_builtin_names(buf)
    if t1_names:
        cmap_names = {**t1_names, **cmap_names}
    return (cff or ttf_names or set(t1_names.values())), cmap_names


def _agl_cp(gname: str) -> int | None:
    """Adobe Glyph List codepoint for a WinAnsi encoding name, if any."""
    if not gname or gname == ".notdef":
        return None
    try:
        from fontTools.agl import toUnicode

        uni = toUnicode(gname)
    except Exception:
        return None
    if uni and len(uni) == 1:
        return ord(uni)
    return None


def _resolve_draw_name(enc_name: str, code: int, present: set[str], cmap_names: dict[int, str]) -> str:
    if enc_name and enc_name in present:
        return enc_name
    mapped = cmap_names.get(code, "")
    if mapped and mapped in present:
        return mapped
    return enc_name or mapped or ""


def _collect_font_rows(doc, font_substr: str, *, all_encoded: bool = False) -> list[dict]:
    from pdf_cmap_fix.content_streams import collect_referenced_gids
    from pdf_cmap_fix.pdf_font_encoding import resolve_simple_encoding

    needle = font_substr.lower()
    simple: set[int] = set()
    wanted: dict[int, str] = {}
    skipped: list[str] = []
    for pno in range(len(doc)):
        for f in doc[pno].get_fonts(full=True):
            xref, _, ftype, name, *_ = f
            if needle not in name.lower():
                continue
            if _is_latin_companion(name):
                if name not in skipped:
                    skipped.append(name)
                continue
            if "bold" not in needle and "bold" in name.lower():
                continue
            wanted[xref] = name
            if ftype in ("Type1", "MMType1", "TrueType"):
                simple.add(xref)
    if skipped:
        print("skipping Latin companion font(s):", ", ".join(skipped))
    used = collect_referenced_gids(doc, simple_xrefs=simple)
    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for xref, pdf_name in wanted.items():
        existing = _tounicode_for_font(doc, xref)
        encoding = resolve_simple_encoding(doc, xref) or {}
        try:
            tup = doc.extract_font(xref)
            buf = bytes(tup[3]) if tup and len(tup) >= 4 and tup[3] else b""
        except Exception:
            buf = b""
        present, cmap_names = _embedded_name_index(buf)
        codes = set(used.get(xref, ()))
        if all_encoded or not codes:
            codes |= {
                c
                for c, gname in encoding.items()
                if gname
                and gname != ".notdef"
                and (
                    not present
                    or gname in present
                    or cmap_names.get(c) in present
                )
            }
            codes |= set(existing)
        for code in sorted(c for c in codes if 0 <= c <= 0xFFFF):
            key = (xref, code)
            if key in seen:
                continue
            seen.add(key)
            enc_name = encoding.get(code, "")
            draw_name = _resolve_draw_name(enc_name, code, present, cmap_names)
            if (
                present
                and draw_name not in present
                and code not in used.get(xref, ())
                and code not in existing
            ):
                continue
            tu = existing.get(code, "")
            agl = _agl_cp(enc_name)
            if tu and len(tu) == 1:
                cp = ord(tu[0])
            elif agl is not None:
                cp = agl
            else:
                cp = code
            rows.append(
                {
                    "font_xref": xref,
                    "pdf_font_name": pdf_name,
                    "table_font": _table_font_name(pdf_name),
                    "code": code,
                    "code_hex": f"{code:02X}" if code <= 0xFF else f"{code:04X}",
                    "tounicode": tu,
                    "lookup_cp": cp,
                    # PFA Gxx and these Distiller CFF subsets are keyed by the
                    # byte the PDF renders, not by an AGL name / ToUnicode CP.
                    "export_cp": (
                        code
                        if any(
                            marker in pdf_name
                            for marker in ("MSTT", "TibetanChosGyal", "TibetanMangala")
                        )
                        else cp
                    ),
                    "glyph_name": draw_name,
                }
            )
    return rows


def _render_page(doc, out_dir: Path, page_no: int = 0) -> str:
    import fitz

    pix = doc[page_no].get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
    name = f"page{page_no}.png"
    pix.save(str(out_dir / name))
    return name


def _load_edits(path: Path) -> dict[tuple[str, int], str]:
    """``{(font_name, lookup_cp): unicode}`` from an exported editor CSV."""
    out: dict[tuple[str, int], str] = {}
    if not path or not path.is_file():
        return out
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh, quotechar='"'):
            if len(row) < 3:
                continue
            font, cp_s, value = row[0], row[1], row[2]
            try:
                cp = int(cp_s)
            except ValueError:
                continue
            out[(font, cp)] = value
    return out


def _first_char_bboxes(
    doc, max_pages: int | None = None
) -> dict[tuple[str, str], tuple[int, object, float]]:
    import fitz

    found: dict[tuple[str, str], tuple[int, object, float]] = {}
    last = len(doc) if max_pages is None else min(len(doc), max_pages)
    for pno in range(last):
        raw = doc[pno].get_text("rawdict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = _font_tail(span.get("font") or "")
                    size = float(span.get("size") or 12)
                    for ch in span.get("chars") or ():
                        text = ch.get("c") or ""
                        key = (font, text)
                        if key in found or not text:
                            continue
                        found[key] = (pno, fitz.Rect(ch["bbox"]), size)
    return found


def _expand_zero_width_clip(rect, size: float, page_rect):
    """Combining marks report ~0 advance; ink sits left of the origin."""
    import fitz

    size = max(float(size), 8.0)
    min_w = size * 0.95
    if rect.width < min_w:
        # Keep the origin at x1 and reach one em to the left.
        rect = fitz.Rect(rect.x1 - min_w, rect.y0, rect.x1 + size * 0.15, rect.y1)
    min_h = size * 1.1
    if rect.height < min_h:
        mid = (rect.y0 + rect.y1) / 2
        rect = fitz.Rect(rect.x0, mid - min_h / 2, rect.x1, mid + min_h / 2)
    pad = max(2.0, size * 0.12)
    rect = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    return rect & page_rect


def _font_buffers(doc, xrefs: set[int]) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    for xref in xrefs:
        try:
            tup = doc.extract_font(xref)
        except Exception:
            out[xref] = b""
            continue
        out[xref] = bytes(tup[3]) if tup and len(tup) >= 4 and tup[3] else b""
    return out


def _glyph_draw_text(row: dict) -> str:
    """Character to send through the embedded font's own names/cmap."""
    tu = row.get("tounicode") or ""
    if tu:
        return tu
    gname = row.get("glyph_name") or ""
    if not gname or gname == ".notdef":
        return ""
    try:
        from fontTools.agl import toUnicode

        uni = toUnicode(gname)
        if uni:
            return uni
    except Exception:
        pass
    return gname if len(gname) == 1 else ""


def _pixmap_has_ink(pix, threshold: int = 80) -> bool:
    try:
        samples = pix.samples
    except Exception:
        return True
    step = pix.n
    # Guides (gray frame, blue baseline) stay above this; glyph ink is near-black.
    for i in range(0, len(samples), step):
        if samples[i] < threshold and samples[i + min(1, step - 1)] < threshold:
            return True
    return False


# Visible in the PNG, but blue enough that ``_pixmap_has_ink`` ignores it.
_BASELINE_COLOR = (0.20, 0.40, 0.86)
_BASELINE_WIDTH = 0.75


def _expand_bounds_to_guide(
    bounds, guide_y: float = 0.0
) -> tuple[float, float, float, float]:
    """Keep the guide (baseline or ka headline) in frame."""
    xmin, ymin, xmax, ymax = bounds
    return xmin, min(ymin, guide_y), xmax, max(ymax, guide_y)


def _stroke_baseline(page, y: float, x0: float, x1: float) -> None:
    import fitz

    page.draw_line(
        fitz.Point(x0, y),
        fitz.Point(x1, y),
        color=_BASELINE_COLOR,
        width=_BASELINE_WIDTH,
    )


@lru_cache(maxsize=8)
def _cff_top(buf: bytes):
    import io

    from fontTools.cffLib import CFFFontSet

    cff = CFFFontSet()
    cff.decompile(io.BytesIO(buf), None)
    return cff[cff.fontNames[0]]


def _glyph_bounds(top, gname: str):
    from fontTools.pens.boundsPen import BoundsPen

    cs = getattr(top, "CharStrings", None)
    if not cs or gname not in cs:
        return None
    pen = BoundsPen(None)
    try:
        cs[gname].draw(pen)
    except Exception:
        return None
    return pen.bounds


def _ttf_glyph_bounds(buf: bytes, gname: str):
    if not _is_sfnt(buf) or not gname or gname == ".notdef":
        return None
    import io

    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont

    try:
        tt = TTFont(io.BytesIO(buf))
        gs = tt.getGlyphSet()
        if gname not in gs:
            return None
        bp = BoundsPen(gs)
        gs[gname].draw(bp)
        return bp.bounds
    except Exception:
        return None


def _outline_ymax(buf: bytes, gname: str) -> float | None:
    """Top of a named glyph in font units (Tibetan headline when gname is ka)."""
    if not buf or not gname or gname == ".notdef":
        return None
    try:
        bounds = _glyph_bounds(_cff_top(buf), gname)
        if bounds:
            return float(bounds[3])
    except Exception:
        pass
    bounds = _ttf_glyph_bounds(buf, gname)
    if bounds:
        return float(bounds[3])
    return None


def _font_upem(buf: bytes) -> float:
    if _is_sfnt(buf):
        try:
            import io

            from fontTools.ttLib import TTFont

            return float(TTFont(io.BytesIO(buf))["head"].unitsPerEm)
        except Exception:
            pass
    return 1000.0


def _render_cff_outline(buf: bytes, gname: str, dest: Path, guide_y: float = 0.0) -> bool:
    """Rasterise a CFF CharString by name (no ToUnicode / insert_text).

    Needed for slots whose ToUnicode is ``<`` / ``>`` (breaks PDF text
    operators and HTML-inlined JSON) or missing entirely.
    """
    if not buf or not gname or gname == ".notdef":
        return False
    import fitz
    from fontTools.pens.recordingPen import RecordingPen

    from pdf_cmap_fix.glyph_shape_id import _flatten

    try:
        top = _cff_top(buf)
        cs = getattr(top, "CharStrings", None)
        if not cs or gname not in cs:
            return False
        rp = RecordingPen()
        cs[gname].draw(rp)
        bounds = _glyph_bounds(top, gname)
        contours = _flatten(rp.value)
    except Exception:
        return False
    if not bounds or not contours:
        return False
    xmin, ymin, xmax, ymax = _expand_bounds_to_guide(bounds, guide_y)
    pad_fu = 80.0
    fw = max(xmax - xmin, 50.0) + 2 * pad_fu
    fh = max(ymax - ymin, 50.0) + 2 * pad_fu
    scale = 72.0 / max(fw, fh)
    page_w = fw * scale
    page_h = fh * scale

    def xy(x: float, y: float) -> tuple[float, float]:
        return ((x - (xmin - pad_fu)) * scale, ((ymax + pad_fu) - y) * scale)

    d = fitz.open()
    try:
        p = d.new_page(width=page_w, height=page_h)
        p.draw_rect(
            fitz.Rect(0.5, 0.5, page_w - 0.5, page_h - 0.5),
            color=(0.82, 0.82, 0.82),
            width=0.4,
        )
        _stroke_baseline(p, xy(0.0, guide_y)[1], 0, page_w)
        shape = p.new_shape()
        for contour in contours:
            if len(contour) < 3:
                continue
            shape.draw_polyline([fitz.Point(*xy(x, y)) for x, y in contour])
            shape.finish(color=(0, 0, 0), fill=(0, 0, 0), closePath=True, width=0)
        shape.commit()
        pix = p.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        if not _pixmap_has_ink(pix):
            return False
        pix.save(str(dest))
        return True
    except Exception:
        return False
    finally:
        d.close()


def _render_ttf_outline(buf: bytes, gname: str, dest: Path, guide_y: float = 0.0) -> bool:
    """Rasterise a TrueType glyf by PostScript name (PDF /Encoding slot)."""
    if not buf or not gname or gname == ".notdef":
        return False
    if not _is_sfnt(buf):
        return False
    import io

    import fitz
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import TTFont

    from pdf_cmap_fix.glyph_shape_id import _flatten

    try:
        tt = TTFont(io.BytesIO(buf))
        if "glyf" not in tt:
            return False
        gs = tt.getGlyphSet()
        if gname not in gs:
            return False
        rp = RecordingPen()
        gs[gname].draw(rp)
        bp = BoundsPen(gs)
        gs[gname].draw(bp)
        bounds = bp.bounds
        contours = _flatten(rp.value)
    except Exception:
        return False
    if not bounds or not contours:
        return False
    xmin, ymin, xmax, ymax = _expand_bounds_to_guide(bounds, guide_y)
    pad_fu = max(80.0, (ymax - ymin) * 0.08, (xmax - xmin) * 0.08)
    fw = max(xmax - xmin, 50.0) + 2 * pad_fu
    fh = max(ymax - ymin, 50.0) + 2 * pad_fu
    scale = 72.0 / max(fw, fh)
    page_w = fw * scale
    page_h = fh * scale

    def xy(x: float, y: float) -> tuple[float, float]:
        return ((x - (xmin - pad_fu)) * scale, ((ymax + pad_fu) - y) * scale)

    d = fitz.open()
    try:
        p = d.new_page(width=page_w, height=page_h)
        p.draw_rect(
            fitz.Rect(0.5, 0.5, page_w - 0.5, page_h - 0.5),
            color=(0.82, 0.82, 0.82),
            width=0.4,
        )
        _stroke_baseline(p, xy(0.0, guide_y)[1], 0, page_w)
        shape = p.new_shape()
        for contour in contours:
            if len(contour) < 3:
                continue
            shape.draw_polyline([fitz.Point(*xy(x, y)) for x, y in contour])
            shape.finish(color=(0, 0, 0), fill=(0, 0, 0), closePath=True, width=0)
        shape.commit()
        pix = p.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        if not _pixmap_has_ink(pix):
            return False
        pix.save(str(dest))
        return True
    except Exception:
        return False
    finally:
        d.close()


def _render_isolated_glyph(
    buf: bytes, text: str, gname: str, dest: Path, guide_y: float = 0.0
) -> bool:
    """Draw one glyph from the embedded program, fitted to its outline box."""
    if not buf:
        return False
    if _render_cff_outline(buf, gname, dest, guide_y=guide_y):
        return True
    if _render_ttf_outline(buf, gname, dest, guide_y=guide_y):
        return True
    # TrueType already tried the real glyf. insert_text would only
    # paint a .notdef tofu for subset-missing WinAnsi names.
    if _is_sfnt(buf):
        return False
    if not text:
        return False
    import fitz

    bounds = None
    if gname and gname != ".notdef":
        try:
            bounds = _glyph_bounds(_cff_top(buf), gname)
        except Exception:
            bounds = None

    pad = 10.0
    fs = 36.0
    upem = 1000.0
    scale = fs / upem
    if bounds and bounds[0] <= bounds[2] and bounds[1] <= bounds[3]:
        xmin, ymin, xmax, ymax = _expand_bounds_to_guide(bounds, guide_y)
        gw = max((xmax - xmin) * scale, 12.0)
        gh = max((ymax - ymin) * scale, 12.0)
        page_w = gw + 2 * pad
        page_h = gh + 2 * pad
        origin_x = pad - xmin * scale
        origin_y = pad + ymax * scale
    else:
        page_w = page_h = 72.0
        origin_x, origin_y = 36.0, 48.0

    d = fitz.open()
    try:
        p = d.new_page(width=page_w, height=page_h)
        p.insert_font(fontname="f", fontbuffer=buf)
        p.draw_rect(
            fitz.Rect(1, 1, page_w - 1, page_h - 1),
            color=(0.82, 0.82, 0.82),
            width=0.4,
        )
        _stroke_baseline(p, origin_y - guide_y * scale, 1, page_w - 1)
        p.insert_text((origin_x, origin_y), text, fontname="f", fontsize=fs)
        pix = p.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        if not _pixmap_has_ink(pix):
            return False
        pix.save(str(dest))
        return True
    except Exception:
        return False
    finally:
        d.close()


def _merge_rows(groups: list[list[dict]]) -> list[dict]:
    """Keep one row per ``(table_font, code)``, preferring a rendered glyph."""
    by_key: dict[tuple[str, int], dict] = {}
    for rows in groups:
        for row in rows:
            key = (row["table_font"], row["code"])
            prev = by_key.get(key)
            if prev is None or (row.get("glyph") and not prev.get("glyph")):
                by_key[key] = row
    return [by_key[k] for k in sorted(by_key)]


class _SimpleFontReplay:
    """Draw a simple-font encoding byte the same way the PDF page does.

    Needed for Type1 PFA (Distiller MSTT): no CFF/glyf we can name-draw, and
    MuPDF ``insert_text`` has no Unicode cmap to those ``Gxx`` slots.
    """

    def __init__(self, src_doc):
        self.src = src_doc
        self._loc: dict[int, tuple[int, str]] = {}
        self._tmps: dict[int, tuple] = {}
        for pno in range(len(src_doc)):
            for f in src_doc[pno].get_fonts(full=True):
                xref = f[0]
                if xref not in self._loc and f[4]:
                    self._loc[xref] = (pno, f[4])

    def render(
        self,
        xref: int,
        code: int,
        dest: Path,
        fontsize: float = 48.0,
        guide_y: float = 0.0,
        upem: float = 1000.0,
    ) -> bool:
        if not (0 <= code <= 255):
            return False
        slot = self._ensure(xref)
        if not slot:
            return False
        import fitz

        tmp, page, cxref, res = slot
        ox, oy = 200.0, 200.0
        guide_pdf_y = oy + guide_y * fontsize / (upem or 1000.0)
        try:
            tmp.update_stream(
                cxref,
                (
                    f"q\n{_BASELINE_COLOR[0]} {_BASELINE_COLOR[1]} "
                    f"{_BASELINE_COLOR[2]} RG\n{_BASELINE_WIDTH} w\n"
                    f"{ox - 40} {guide_pdf_y} m\n{ox + 60} {guide_pdf_y} l\nS\nQ\n"
                    f"q\nBT\n/{res} {fontsize} Tf\n{ox} {oy} Td\n"
                    f"<{code:02X}> Tj\nET\nQ\n"
                ).encode(),
            )
            origin = fitz.Point(ox, oy) * page.transformation_matrix
            clip = (
                fitz.Rect(
                    origin.x - 36,
                    origin.y - 48,
                    origin.x + 56,
                    origin.y + 40,
                )
                & page.rect
            )
            if clip.is_empty:
                return False
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=clip, alpha=False)
            if not _pixmap_has_ink(pix):
                return False
            pix.save(str(dest))
            return True
        except Exception:
            return False

    def _ensure(self, xref: int):
        cached = self._tmps.get(xref)
        if cached is not None:
            return cached
        loc = self._loc.get(xref)
        if not loc:
            return None
        import fitz

        pno, res = loc
        tmp = fitz.open()
        tmp.insert_pdf(self.src, from_page=pno, to_page=pno)
        page = tmp[0]
        contents = page.get_contents()
        if not contents:
            tmp.close()
            return None
        self._tmps[xref] = (tmp, page, contents[0], res)
        return self._tmps[xref]

    def close(self) -> None:
        for tmp, *_ in self._tmps.values():
            tmp.close()
        self._tmps.clear()


def _bbox_lookup_keys(row: dict) -> list[tuple[str, str]]:
    """Page-extract keys: ToUnicode first, then the encoding byte as a char."""
    font = _font_tail(row["pdf_font_name"])
    keys: list[tuple[str, str]] = []
    tu = row.get("tounicode") or ""
    if tu:
        keys.append((font, tu))
    code = row.get("code")
    if isinstance(code, int) and 0 <= code <= 0x10FFFF:
        keys.append((font, chr(code)))
    return keys


def _headline_guide_ys(
    rows: list[dict], bufs: dict[int, bytes], headline_code: int | None
) -> dict[int, float]:
    """Per-xref font-unit y of the top of ``headline_code`` (usually ka)."""
    if headline_code is None:
        return {}
    out: dict[int, float] = {}
    for row in rows:
        xref = row["font_xref"]
        if xref in out or row["code"] != headline_code:
            continue
        y = _outline_ymax(bufs.get(xref, b""), row.get("glyph_name") or "")
        if y is not None:
            out[xref] = y
            print(
                f"headline code {headline_code} ({row['code_hex']}) "
                f"{row['pdf_font_name']} /{row.get('glyph_name')} y={y:.1f}"
            )
    return out


def _render_glyphs(
    doc,
    rows: list[dict],
    out_dir: Path,
    *,
    clear: bool = True,
    stem_prefix: str = "",
    headline_code: int | None = None,
) -> None:
    """Isolated font glyph (primary) plus the first on-page crop (context)."""
    import fitz

    glyph_dir = out_dir / "glyphs"
    if clear and glyph_dir.is_dir():
        for old in glyph_dir.glob("*.png"):
            old.unlink()
    glyph_dir.mkdir(exist_ok=True)
    bboxes = _first_char_bboxes(doc)
    bufs = _font_buffers(doc, {row["font_xref"] for row in rows})
    guides = _headline_guide_ys(rows, bufs, headline_code)
    replay = _SimpleFontReplay(doc)
    try:
        for row in rows:
            stem = f"{stem_prefix}{row['font_xref']}_{row['code']:04X}"
            row["glyph"] = ""
            row["glyph_pdf"] = ""
            font_png = glyph_dir / f"{stem}_font.png"
            buf = bufs.get(row["font_xref"], b"")
            guide_y = guides.get(row["font_xref"], 0.0)
            if not _is_sfnt(buf):
                # Bare CFF/PFA glyph names can be stale or deliberately
                # scrambled. Replay the byte through the PDF's own font
                # resource; that is what is actually visible on the page.
                rendered = replay.render(
                    row["font_xref"],
                    row["code"],
                    font_png,
                    guide_y=guide_y,
                    upem=_font_upem(buf),
                )
            else:
                rendered = _render_isolated_glyph(
                    buf,
                    _glyph_draw_text(row),
                    row.get("glyph_name") or "",
                    font_png,
                    guide_y=guide_y,
                ) or replay.render(
                    row["font_xref"],
                    row["code"],
                    font_png,
                    guide_y=guide_y,
                    upem=_font_upem(buf),
                )
            if rendered:
                row["glyph"] = f"glyphs/{stem}_font.png"
            hit = None
            for key in _bbox_lookup_keys(row):
                hit = bboxes.get(key)
                if hit is not None:
                    break
            if hit is None:
                continue
            pno, rect, size = hit
            clip = _expand_zero_width_clip(rect, size, doc[pno].rect)
            if clip.is_empty or clip.width < 1 or clip.height < 1:
                continue
            pix = doc[pno].get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip)
            pdf_png = glyph_dir / f"{stem}_pdf.png"
            pix.save(str(pdf_png))
            row["glyph_pdf"] = f"glyphs/{stem}_pdf.png"
    finally:
        replay.close()


def _compare(tables: dict[str, dict[str, dict[int, str]]], fonts: list[str]) -> None:
    print("=== table compare (Sheja masthead hints) ===")
    print(f"{'char':<8} {'cp':>6} {'hint':<8}", end="")
    cols: list[tuple[str, str]] = []
    for src, by_font in tables.items():
        for font in fonts:
            if font in by_font:
                cols.append((src, font))
                print(f"  {src[:4]}:{font[:18]:<18}", end="")
    print()
    hits = {col: 0 for col in cols}
    for latin, hint in SHEJA_HINTS.items():
        cp = ord(latin)
        print(f"{latin!r:<8} {cp:6d} {hint:<8}", end="")
        for col in cols:
            src, font = col
            val = tables[src][font].get(cp)
            mark = "✓" if val == hint else "·"
            if val == hint:
                hits[col] += 1
            shown = (val or "—").replace("\n", " ")
            print(f"  {mark} {shown:<20}", end="")
        print()
    print("\nhits vs masthead hints:")
    for col, n in sorted(hits.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])):
        print(f"  {n}/{len(SHEJA_HINTS)}  {col[0]} / {col[1]}")

    sample = "zÙh-Mæ-æÙ-"
    print("\n=== remap of extracted masthead", repr(sample), "===")
    for src, font in cols:
        parts = []
        for ch in sample:
            val = tables[src][font].get(ord(ch))
            parts.append(val if val else f"[{ch}]")
        print(f"  {src}/{font}: {''.join(parts)}")


_APP_JS = r"""
const srcs = DATA.sources;
const tbody = document.getElementById("rows");
const statusEl = document.getElementById("status");
const fontLabel = document.getElementById("fontLabel");
if (fontLabel) {
  const fonts = [...new Set((DATA.rows || []).map((r) => r.table_font).filter(Boolean))];
  fontLabel.textContent = (fonts.join(", ") || "font") + " — glyph vs Unicode mapping";
}

const LONE_VOWEL = {
  o: "\u0f7c", i: "\u0f72", u: "\u0f74", e: "\u0f7a",
  A: "\u0f71", I: "\u0f80", ai: "\u0f7b", au: "\u0f7d"
};
const MARK_EWTS = Object.fromEntries(
  Object.entries(LONE_VOWEL).map(([k, v]) => [v, k])
);
const TSHEG = "\u0f0b";
const ACHEN = "\u0f68";

function toSubjoined(uni) {
  let out = "";
  for (const ch of uni) {
    const cp = ch.codePointAt(0);
    out += (cp >= 0x0f40 && cp <= 0x0f6c) ? String.fromCodePoint(cp + 0x50) : ch;
  }
  return out;
}

function fromSubjoined(uni) {
  let out = "";
  for (const ch of uni) {
    const cp = ch.codePointAt(0);
    out += (cp >= 0x0f90 && cp <= 0x0fbc) ? String.fromCodePoint(cp - 0x50) : ch;
  }
  return out;
}

function isIsolatedSubjoined(uni) {
  if (!uni) return false;
  let saw = false;
  for (const ch of uni) {
    const cp = ch.codePointAt(0);
    if (cp >= 0x0f90 && cp <= 0x0fbc) saw = true;
    else if (!(cp >= 0x0f71 && cp <= 0x0f84) && cp !== 0x0fb7) return false;
  }
  return saw;
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function conv() {
  return window.jsEWTS || null;
}

function ewtsToUni(ewts) {
  if (ewts === " " || ewts === "*") return TSHEG;
  const c = conv();
  if (!c) return ewts;
  const warns = [];
  const trimmed = ewts.trim();
  if (trimmed.startsWith("+") && trimmed.length > 1) {
    const base = c.fromWylie(trimmed.slice(1), { sloppy: true }, warns);
    const sub = toSubjoined(base.charAt(0) === ACHEN && trimmed !== "+a" ? base.slice(1) : base);
    if (sub) return { uni: sub, warns };
  }
  let uni = c.fromWylie(ewts, { sloppy: true }, warns);
  if (LONE_VOWEL[trimmed] && uni.charAt(0) === ACHEN) {
    uni = uni.slice(1);
  }
  return { uni, warns };
}

function uniToEwts(uni) {
  if (uni === TSHEG) return " ";
  if (MARK_EWTS[uni]) return MARK_EWTS[uni];
  const c = conv();
  if (!c || !uni) return "";
  if (isIsolatedSubjoined(uni)) {
    const w = c.toWylie(fromSubjoined(uni));
    return w ? "+" + w : "";
  }
  return c.toWylie(uni);
}

function parseEdit(value, mode) {
  if (mode === "ewts") {
    const out = ewtsToUni(value);
    if (typeof out === "string") return { uni: out, warns: [] };
    return out;
  }
  return { uni: value, warns: [] };
}

function formatEdit(uni, mode) {
  if (mode === "ewts") return uniToEwts(uni);
  return uni;
}

function currentMode() {
  const el = document.querySelector("input[name=editMode]:checked");
  return el ? el.value : "unicode";
}

function refreshRow(inp) {
  const mode = currentMode();
  const { uni, warns } = parseEdit(inp.value, mode);
  inp.dataset.unicode = uni || "";
  const preview = inp.parentElement.querySelector(".preview");
  if (mode === "ewts") {
    preview.textContent = uni || "";
    preview.className = "preview tib";
  } else {
    preview.textContent = uni ? uniToEwts(uni) : "";
    preview.className = "preview ewts";
  }
  inp.classList.toggle("warn", !!(warns && warns.length) && !uni);
  inp.title = (warns && warns.length) ? warns.join(" ") : "";
}

function refreshAll() {
  const mode = currentMode();
  for (const inp of document.querySelectorAll("input.edit")) {
    const uni = inp.dataset.unicode || "";
    inp.value = formatEdit(uni, mode);
    refreshRow(inp);
  }
  const hint = document.getElementById("modeHint");
  hint.textContent = mode === "ewts"
    ? "Type EWTS (rgya, la, o, +ka). +ka is subjoined ྐ. Lone vowels drop a-chen so o → ོ. Space is tsheg."
    : "Type Unicode. The preview shows EWTS (+ka for a subjoined letter).";
}

for (const r of DATA.rows) {
  const tr = document.createElement("tr");
  let srcCells = "";
  for (const s of srcs) {
    const v = (r.sources[s] || "");
    const cls = (r.hint && v === r.hint) ? "hit" : (v ? "miss" : "");
    srcCells += "<td class=\"" + cls + "\">" + esc(v) + "</td>";
  }
  const initial = r.edit || r.hint || r.sources[srcs[0]] || "";
  const fontImg = r.glyph
    ? "<img class=\"glyph font\" src=\"" + esc(r.glyph) + "\" title=\"embedded font\">"
    : "";
  const pdfImg = r.glyph_pdf
    ? "<img class=\"glyph pdf\" src=\"" + esc(r.glyph_pdf) + "\" title=\"PDF crop\">"
    : "";
  const gname = r.glyph_name ? "<div class=\"gname\">/" + esc(r.glyph_name) + "</div>" : "";
  tr.id = "row-" + r.code;
  tr.dataset.code = String(r.code);
  tr.innerHTML =
    "<td>" + esc(r.table_font || r.pdf_font_name) +
      "<div class=\"gname\">" + esc(r.pdf_font_name) + "</div></td>" +
    "<td id=\"code-" + r.code + "\"><b>" + r.code + "</b> <code>" + esc(r.code_hex) + "</code></td>" +
    "<td>" + esc(r.tounicode) + " <code>U+" +
      r.lookup_cp.toString(16).toUpperCase() + "</code></td>" +
    "<td>" + fontImg + gname + "</td>" +
    "<td>" + pdfImg + "</td>" +
    "<td class=\"edit-cell\"><input class=\"edit\" data-cp=\"" +
      (r.export_cp === undefined ? r.lookup_cp : r.export_cp) +
      "\" data-font=\"" + esc(r.table_font || "") +
      "\" data-unicode=\"" + esc(initial) + "\" value=\"" + esc(initial) +
      "\"><span class=\"preview\"></span></td>" +
    "<td>" + esc(r.hint || "") + "</td>" +
    srcCells;
  tbody.appendChild(tr);
}

for (const inp of document.querySelectorAll("input.edit")) {
  inp.addEventListener("input", () => refreshRow(inp));
}
for (const radio of document.querySelectorAll("input[name=editMode]")) {
  radio.addEventListener("change", refreshAll);
}
refreshAll();

document.getElementById("jumpCode").addEventListener("input", (e) => {
  const raw = e.target.value.trim();
  for (const tr of tbody.rows) tr.style.outline = "";
  if (!raw) return;
  const dec = /^(0x)?[0-9a-f]+$/i.test(raw) && /[a-f]/i.test(raw)
    ? parseInt(raw.replace(/^0x/i, ""), 16)
    : parseInt(raw, 10);
  if (Number.isNaN(dec)) return;
  const tr = document.getElementById("row-" + dec);
  if (!tr) {
    statusEl.textContent = "no row for code " + dec;
    return;
  }
  tr.style.outline = "2px solid #1a73e8";
  tr.scrollIntoView({ block: "center" });
  statusEl.textContent = "code " + dec;
});

document.getElementById("exportBtn").onclick = () => {
  const override = document.getElementById("exportName").value.trim();
  const mode = currentMode();
  const rows = [];
  for (const inp of document.querySelectorAll("input.edit")) {
    const { uni } = parseEdit(inp.value, mode);
    if (!uni) continue;
    const font = override || inp.dataset.font || "TB-Youtso";
    rows.push([font, Number(inp.dataset.cp), uni]);
  }
  rows.sort((a, b) => a[0].localeCompare(b[0]) || a[1] - b[1]);
  const lines = rows.map(([font, cp, val]) => font + "," + cp + "," + val);
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (override || "youtso_tables").replace(/\s+/g, "_") + ".csv";
  a.click();
  statusEl.textContent = "exported " + lines.length + " Unicode rows";
};
"""


def _copy_jsewts(out_dir: Path, src: Path) -> bool:
    if src.is_file():
        shutil.copy2(src, out_dir / "jsewts.js")
        return True
    return False


def _write_html(out_dir: Path, payload: dict, jsewts_ok: bool) -> None:
    src_head = "".join(f"<th>{s}</th>" for s in payload["sources"])
    page = payload.get("page_image") or ""
    # Raw "<" inside <script> can make the HTML parser drop the rest of
    # DATA (ToUnicode of codes 60/62 is "<" / ">").
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    jsewts_tag = '<script src="jsewts.js"></script>' if jsewts_ok else (
        '<script>var module={exports:{}};var exports=module.exports;</script>'
        '<script src="https://cdn.jsdelivr.net/npm/jsewts@1.1.0/src/jsewts.js"></script>'
        "<script>window.jsEWTS=module.exports.jsEWTS||module.exports;</script>"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Legacy font table editor</title>
<style>
  body {{ font: 14px/1.4 system-ui, sans-serif; margin: 16px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 6px; vertical-align: middle; }}
  th {{ position: sticky; top: 0; background: #f4f4f4; z-index: 1; }}
  img.glyph {{ background: #fff; }}
  img.glyph.font {{ height: 72px; }}
  img.glyph.pdf {{ height: 36px; }}
  .gname {{ font-size: 11px; color: #666; }}
  img.page {{ max-width: 100%; border: 1px solid #ddd; }}
  input.edit {{ width: 8em; font-size: 16px; }}
  input.edit.warn {{ background: #ffe8e8; }}
  .edit-cell {{ min-width: 9em; }}
  .preview {{ display: block; margin-top: 2px; min-height: 1.2em; }}
  .preview.tib {{ font-size: 20px; }}
  .preview.ewts {{ font-family: ui-monospace, monospace; font-size: 12px; color: #555; }}
  .hit {{ background: #e8ffe8; }}
  .miss {{ background: #fff3e0; }}
  .toolbar {{ margin: 12px 0; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .mode {{ display: flex; gap: 10px; align-items: center; padding: 4px 8px; background: #f4f4f4; border-radius: 6px; }}
  #modeHint {{ color: #555; }}
  code {{ background: #f0f0f0; padding: 1px 4px; }}
</style>
</head>
<body>
<h1>Legacy font table editor</h1>
<p id="fontLabel"></p>
<p>Generated by <code>scripts/misc/edit_legacy_font_table.py</code>.
The isolated glyph is the visual truth; <code>vendored-tiblegenc</code> is the
shipped mapping (full stacks). <code>attu</code> is the old decomposed table
kept for comparison. The blue line is {payload.get("guide_label") or "the font baseline"}.
Edit a cell and export CSV. Do not commit this folder.</p>
<div class="toolbar">
  <div class="mode">
    <label><input type="radio" name="editMode" value="unicode" checked> Unicode</label>
    <label><input type="radio" name="editMode" value="ewts"> EWTS</label>
  </div>
  <span id="modeHint"></span>
  <label>Override export font name
    <input id="exportName" value="" placeholder="empty = per-row name">
  </label>
  <button id="exportBtn" type="button">Download CSV</button>
  <label>Jump to code
    <input id="jumpCode" type="search" placeholder="60 or 3C" style="width:6em">
  </label>
  <span id="status"></span>
</div>
<p><img class="page" src="{page}" alt="PDF page"></p>
<table>
<thead>
<tr>
  <th>font</th><th>code</th><th>ToUnicode</th><th>font glyph</th><th>PDF crop</th><th>edit</th><th>hint</th>
  {src_head}
</tr>
</thead>
<tbody id="rows"></tbody>
</table>
<script>const DATA = {data};</script>
{jsewts_tag}
<script src="app.js"></script>
</body>
</html>
"""
    (out_dir / "app.js").write_text(_APP_JS.lstrip("\n"), encoding="utf-8")
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--pdf",
        type=Path,
        nargs="+",
        required=True,
        help="One or more PDFs; rows are merged by (font, code) so later files can add extra subset glyphs",
    )
    p.add_argument("--font-substr", default="Youtso")
    p.add_argument("--tables", type=Path, default=DEFAULT_TABLES)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--page", type=int, default=0)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--no-vendored",
        action="store_true",
        help="Do not also load pdf_cmap_fix/data/pytiblegenc/*.csv",
    )
    p.add_argument(
        "--jsewts",
        type=Path,
        default=DEFAULT_JSEWTS,
        help="jsewts.js to copy into the UI (browser EWTS conversion)",
    )
    p.add_argument(
        "--edits",
        type=Path,
        default=None,
        help="Previously exported CSV (font,cp,value) to preload in the edit column",
    )
    p.add_argument(
        "--edits-font",
        default=None,
        help="Fallback font name in --edits when the PDF face differs (e.g. TB-Youtso for Bold)",
    )
    p.add_argument(
        "--all-encoded",
        action="store_true",
        default=True,
        help="Include every encoded glyph, not only codes used on the page",
    )
    p.add_argument(
        "--used-only",
        action="store_true",
        help="Only list codes that appear in content streams",
    )
    p.add_argument(
        "--headline-code",
        type=lambda s: int(s, 16) if s.lower().startswith("0x") else int(s),
        default=None,
        help="Draw the blue guide at the top of this encoding byte "
        "(e.g. 43 / 0x2B for TCRC ka) instead of y=0",
    )
    args = p.parse_args(argv)

    for pdf in args.pdf:
        if not pdf.is_file():
            print(f"PDF not found: {pdf}", file=sys.stderr)
            return 2
    sys.path.insert(0, str(ROOT))

    tables = _load_tables(args.tables)
    if not args.no_vendored:
        vendored = ROOT / "pdf_cmap_fix" / "data" / "pytiblegenc"
        for stem, by_font in _load_tables(vendored).items():
            tables.setdefault(f"vendored-{stem}", by_font)
    fonts = _matching_fonts(tables, args.font_substr)
    print(f"table sources: {', '.join(tables) or '(none)'}")
    print(f"matching font names: {', '.join(fonts) or '(none)'}")

    all_encoded = args.all_encoded and not args.used_only
    args.out.mkdir(parents=True, exist_ok=True)
    grouped: list[list[dict]] = []
    page_image = ""
    for i, pdf in enumerate(args.pdf):
        doc = _open_pdf(pdf)
        try:
            print(f"reading {pdf}")
            part = _collect_font_rows(doc, args.font_substr, all_encoded=all_encoded)
            if i == 0:
                page_image = _render_page(doc, args.out, args.page)
            _render_glyphs(
                doc,
                part,
                args.out,
                clear=(i == 0),
                stem_prefix=f"{i}_",
                headline_code=args.headline_code,
            )
            grouped.append(part)
        finally:
            doc.close()
    rows = _merge_rows(grouped)
    print(f"merged {sum(len(g) for g in grouped)} collected rows → {len(rows)} unique (font, code)")

    used_fonts = {row["table_font"] for row in rows if row.get("table_font")}
    fonts = [f for f in fonts if f in used_fonts]
    source_stems = [s for s in ("vendored-tiblegenc", "attu") if s in tables]
    if not source_stems:
        source_stems = [s for s in tables if any(f in tables[s] for f in fonts)]
    sources = []
    for src in source_stems:
        for font in fonts:
            if font in tables[src]:
                sources.append(f"{src}/{font}")

    edits = _load_edits(args.edits) if args.edits else {}
    if args.edits:
        print(f"preloading {len(edits)} edits from {args.edits}")
    fallback_fonts = []
    if args.edits_font:
        fallback_fonts.append(args.edits_font)
    fallback_fonts.extend(("TB-Youtso", "TB2-Youtso"))
    for row in rows:
        cp = row["lookup_cp"]
        hints = CALLIGRAPHIC_HINTS if "calligraph" in args.font_substr.lower() else SHEJA_HINTS
        row["hint"] = hints.get(row["tounicode"])
        row["edit"] = edits.get((row["table_font"], cp))
        if row["edit"] is None:
            for fb in fallback_fonts:
                row["edit"] = edits.get((fb, cp))
                if row["edit"] is not None:
                    break
        row["sources"] = {}
        for src in source_stems:
            by_font = tables[src]
            for font in fonts:
                if font not in by_font:
                    continue
                row["sources"][f"{src}/{font}"] = by_font[font].get(cp, "")
        if row["edit"] is None:
            vendored = tables.get("vendored-tiblegenc") or tables.get("tiblegenc") or {}
            row["edit"] = (vendored.get(row["table_font"]) or {}).get(cp, "")

    _compare(tables, fonts)

    if args.headline_code is not None:
        guide_label = (
            f"the top of code {args.headline_code} / "
            f"{args.headline_code:02X}, not the font baseline"
        )
    else:
        guide_label = "the font baseline"
    payload = {
        "pdf": [str(p) for p in args.pdf],
        "page_image": page_image,
        "sources": sources,
        "rows": rows,
        "guide_label": guide_label,
    }
    (args.out / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    jsewts_ok = _copy_jsewts(args.out, args.jsewts)
    if jsewts_ok:
        print(f"copied jsewts from {args.jsewts}")
    else:
        print(f"jsewts not found at {args.jsewts}; UI will try the CDN copy")
    _write_html(args.out, payload, jsewts_ok)
    print(f"\nUI written to {args.out / 'index.html'}  ({len(rows)} codes)")

    if args.serve:
        handler = SimpleHTTPRequestHandler
        os_cwd = Path.cwd()
        try:
            import os

            os.chdir(args.out)
            httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
            print(f"serving http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            os.chdir(os_cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
