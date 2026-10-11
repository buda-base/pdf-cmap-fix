"""
Tier 3 (outline hash → Unicode) PDF ToUnicode patching and text extraction.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import fitz

from pdf_cmap_fix import tounicode_core as _core

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FONT_LOOKUP_GSHAPE_DIR = _DATA_DIR / "font_lookup_gshape"

_TIER = "gshape"


def collect_font_merges(
    doc: fitz.Document,
    *,
    font_lookup_dir: Optional[Path] = None,
    verbose: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    lookup_dir = (
        Path(font_lookup_dir).expanduser().resolve()
        if font_lookup_dir is not None
        else FONT_LOOKUP_GSHAPE_DIR.resolve()
    )
    return _core.collect_font_merges(doc, lookup_dir=lookup_dir, tier=_TIER, verbose=verbose)


def patch_doc(
    doc: fitz.Document,
    *,
    font_lookup_dir: Optional[Path] = None,
    verbose: bool = False,
    actualtext: _core.ActualTextMode = "keep",
) -> dict[str, int]:
    lookup_dir = (
        Path(font_lookup_dir).expanduser().resolve()
        if font_lookup_dir is not None
        else FONT_LOOKUP_GSHAPE_DIR.resolve()
    )
    return _core.patch_doc(
        doc,
        lookup_dir=lookup_dir,
        tier=_TIER,
        verbose=verbose,
        actualtext=actualtext,
    )


def extract_all(doc: fitz.Document, *, ignore_actualtext: bool = False) -> str:
    return _core.extract_all(doc, ignore_actualtext=ignore_actualtext)


def build_tounicode_dict(
    pdf_path,
    *,
    font_lookup_dir: Optional[Path] = None,
) -> dict[str, Any]:
    lookup_dir = (
        Path(font_lookup_dir).expanduser().resolve()
        if font_lookup_dir is not None
        else FONT_LOOKUP_GSHAPE_DIR.resolve()
    )
    return _core.build_tounicode_dict(pdf_path, lookup_dir=lookup_dir, tier=_TIER)


def patch_pdf(
    pdf_path,
    output_path=None,
    write_file: bool = True,
    *,
    font_lookup_dir: Optional[Path] = None,
    verbose: bool = False,
    actualtext: _core.ActualTextMode = "keep",
) -> dict:
    lookup_dir = (
        Path(font_lookup_dir).expanduser().resolve()
        if font_lookup_dir is not None
        else FONT_LOOKUP_GSHAPE_DIR.resolve()
    )
    return _core.patch_pdf(
        pdf_path,
        output_path=output_path,
        write_file=write_file,
        lookup_dir=lookup_dir,
        tier=_TIER,
        verbose=verbose,
        actualtext=actualtext,
    )


def extract_pdf_text(
    pdf_path,
    output_dir=None,
    write_files: bool = True,
    *,
    font_lookup_dir: Optional[Path] = None,
    verbose: bool = False,
    actualtext: _core.ActualTextMode = "keep",
) -> dict:
    lookup_dir = (
        Path(font_lookup_dir).expanduser().resolve()
        if font_lookup_dir is not None
        else FONT_LOOKUP_GSHAPE_DIR.resolve()
    )
    return _core.extract_pdf_text(
        pdf_path,
        output_dir=output_dir,
        write_files=write_files,
        lookup_dir=lookup_dir,
        tier=_TIER,
        verbose=verbose,
        actualtext=actualtext,
    )


USAGE = (
    "Usage:\n"
    "  pdf-cmap-fix-gshape [--font-lookup-dir DIR] <pdf1> [pdf2] ...\n"
    "      outline-hash → Unicode lookups from DIR (default: pdf_cmap_fix/data/font_lookup_gshape)\n"
    "  pdf-cmap-fix-gshape [--actualtext keep|auto|strip] <pdf> ...\n"
    "  pdf-cmap-fix-gshape [--font-lookup-dir DIR] --patch-pdf <pdf> ...\n"
    "  pdf-cmap-fix-gshape [--font-lookup-dir DIR] --dump-cmap OUT.json <pdf> ...\n"
)


def main() -> None:
    _core.cli_main(
        sys.argv[1:],
        tier=_TIER,
        default_lookup_dir=FONT_LOOKUP_GSHAPE_DIR,
        usage_text=USAGE,
        program_label="pdf-cmap-fix-gshape",
    )


if __name__ == "__main__":
    main()
