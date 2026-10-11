"""
Tier 1 (GID → Unicode) PDF ToUnicode patching and text extraction.

CLI and defaults match the historical ``pdf_cmap_fix.extractor`` surface.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import fitz

from pdf_cmap_fix import tounicode_core as _core

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Tier-1 (gid) lookup DB: font-ID-keyed JSON + a ``_name_index.json`` that maps
# real name-table names to those IDs (built by
# ``scripts/gid/rebuild_indexed_lookup.py``). This replaced the legacy
# filename-stem-keyed ``font_lookup`` directory whose fuzzy name matching caused
# wrong picks (e.g. TimesNewRomanPSMT -> the "Ma"/Mantra font).
FONT_LOOKUP_DIR = _DATA_DIR / "font_lookup_byid"

_TIER = "gid"


def collect_font_merges(
    doc: fitz.Document,
    *,
    font_lookup_dir: Optional[Path] = None,
    verbose: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    lookup_dir = (
        Path(font_lookup_dir).expanduser().resolve()
        if font_lookup_dir is not None
        else FONT_LOOKUP_DIR.resolve()
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
        else FONT_LOOKUP_DIR.resolve()
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
        else FONT_LOOKUP_DIR.resolve()
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
        else FONT_LOOKUP_DIR.resolve()
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
        else FONT_LOOKUP_DIR.resolve()
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
    "  pdf-cmap-fix [--strategy auto|gid|gid-pua-free|gname|gname-pua-free|gshape|gshape-pua-free] <pdf1> [pdf2] ...\n"
    "      auto is the default and picks the patched extraction with the most Tibetan text\n"
    "  pdf-cmap-fix [--no-auto] [--pua-free] [--gshape] [--gshape-pua-free] <pdf1> [pdf2] ...\n"
    "  pdf-cmap-fix [--font-lookup-dir DIR] <pdf1> [pdf2] ...\n"
    "      Custom lookup dir disables auto and uses the GID tier\n"
    "  pdf-cmap-fix [strategy options] [--actualtext keep|auto|strip] <pdf> ...\n"
    "      keep is the default; auto strips only strongly detected corruption\n"
    "  pdf-cmap-fix [strategy options] --patch-pdf <pdf> ...\n"
    "  pdf-cmap-fix [strategy options] --dump-cmap OUT.json <pdf> ...\n"
)


def main() -> None:
    _core.cli_main(
        sys.argv[1:],
        tier=_TIER,
        default_lookup_dir=FONT_LOOKUP_DIR,
        usage_text=USAGE,
        program_label="pdf-cmap-fix",
        strategy_specs=_core._default_strategy_specs(FONT_LOOKUP_DIR),
    )


if __name__ == "__main__":
    main()
