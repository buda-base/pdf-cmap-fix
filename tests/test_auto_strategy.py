"""Regression tests for CLI auto strategy selection."""
from __future__ import annotations

from pathlib import Path

from pdf_cmap_fix.gid.extractor import FONT_LOOKUP_DIR
from pdf_cmap_fix.tounicode_core import (
    _default_strategy_specs,
    _quality_sort_key,
    _select_auto_strategy,
    _text_quality,
    extract_pdf_text,
)


REPO = Path(__file__).resolve().parents[1]
ISSUE16_PAGE = REPO / "tests" / "fixtures" / "issue16-page1.pdf"


def test_auto_strategy_produces_pua_free_text_for_issue16_page() -> None:
    specs = _default_strategy_specs(FONT_LOOKUP_DIR)
    gid_tier, gid_lookup = specs["gid"]
    baseline = extract_pdf_text(
        ISSUE16_PAGE,
        write_files=False,
        lookup_dir=gid_lookup,
        tier=gid_tier,
        verbose=False,
    )

    selected, selected_tier, selected_lookup, result = _select_auto_strategy(
        ISSUE16_PAGE,
        strategies=specs,
        verbose=False,
    )

    assert result is not None
    # Compatibility-aware same-name release selection can make the default GID
    # result tie the explicitly PUA-free strategies.
    assert selected in {"gid", "gid-pua-free", "gshape", "gshape-pua-free"}
    assert selected_tier in {"gid", "gshape"}
    assert selected_lookup.is_dir()
    assert _quality_sort_key(_text_quality(result["patched"])) >= _quality_sort_key(
        _text_quality(baseline["patched"])
    )
    assert "གང་ཐུགས་བདེ་ཆེན" in result["patched"]
    assert not any("\u0e00" <= c <= "\u0e7f" for c in result["patched"])
