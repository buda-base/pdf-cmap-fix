"""Tier-1 GID maps must not overwrite a ToUnicode that is already correct.

Real-world case: ``Opening and Concluding Prayers`` (Padmakara, InDesign) embeds
both legacy ANSI Tibetan (``TibetanChogyal``) and four *different releases* of
the Unicode ``TibetanChogyalUnicode`` family. The legacy faces were repaired
correctly, but the name matcher collapsed the Unicode releases onto one DB entry
(``tibetanchogyalunicodeid`` and ``tibetanchogyalunicode130917u`` both resolve to
``tibetanchogyalunicode`` via the prefix rule), and ``_merge`` then applied that
map unconditionally -- replacing correct Tibetan with values from an unrelated
glyph space (``རྟ`` -> U+F41B, ``༈`` -> ``༉``). 126 already-correct lines broke.

Tier-1 keys are raw GIDs, so they only transfer when the embedded font program
shares the DB font's glyph order; a name match cannot establish that. The fonts
involved are not redistributable, so these tests exercise the decision function
directly rather than through a PDF fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz

from pdf_cmap_fix.gid.extractor import FONT_LOOKUP_DIR
from pdf_cmap_fix.tounicode_core import (
    MIN_GID_CONFLICTS,
    apply_font_merges_to_doc,
    collect_font_merges,
    extract_all,
    _gid_map_corroborated,
    _is_tibetan_text,
    _pick_db_map_via_index,
)

MONLAM4_PAGE = (
    Path(__file__).resolve().parent / "fixtures" / "kunsang-monlam4-page1.pdf"
)


def test_is_tibetan_text_separates_unicode_from_legacy_stand_ins() -> None:
    assert _is_tibetan_text("ཀ")
    assert _is_tibetan_text("སྒྲུབ")
    # Legacy ANSI fonts map codes to Latin-ish stand-ins -- not real Unicode.
    assert not _is_tibetan_text("!")
    assert not _is_tibetan_text("7'Ü#")
    assert not _is_tibetan_text("")
    # PUA is outside the Tibetan block and must not count as protected text.
    assert not _is_tibetan_text("")
    # Mixed content is not wholly Tibetan.
    assert not _is_tibetan_text("ཀa")


def test_rejects_map_from_a_different_glyph_order() -> None:
    # Shape of the real failure: every Tibetan entry contradicted, none agree.
    existing = {13: "༈", 16: "་", 18: "།", 782: "རྟ", 820: "ལྷ"}
    db_map = {13: "༉", 16: "༌", 18: "༏", 782: "", 820: ""}

    assert not _gid_map_corroborated(existing, db_map)


def test_accepts_stacked_syllable_decomposition() -> None:
    # The tool's primary purpose: the DB extends what the PDF only partly had.
    existing = {40: "ས", 41: "བ", 42: "ག", 43: "མ", 44: "ད"}
    db_map = {40: "སྒྲུབ", 41: "བསྐྱེད", 42: "གྲུབ", 43: "མཁྱེན", 44: "དགྲ"}

    assert _gid_map_corroborated(existing, db_map)


def test_accepts_map_that_merely_agrees() -> None:
    existing = {5: "ༀ", 6: "ཀ", 7: "ཁ", 8: "ག", 9: "ང"}

    assert _gid_map_corroborated(existing, dict(existing))


def test_single_corroboration_is_enough_to_accept() -> None:
    # Deliberately conservative: we only reject on *zero* corroboration, so a
    # genuine map with a noisy tail is never dropped.
    existing = {40: "ས", 13: "༈", 16: "་", 18: "།", 782: "རྟ"}
    db_map = {40: "སྒྲུབ", 13: "༉", 16: "༌", 18: "༏", 782: ""}

    assert _gid_map_corroborated(existing, db_map)


def test_legacy_font_entries_are_not_protected() -> None:
    # A legacy ANSI face holds Latin stand-ins; replacing them wholesale is the
    # correct repair, so the guard must stay inert here.
    existing = {33: "!", 34: '"', 35: "#", 36: "$", 37: "%", 38: "&"}
    db_map = {33: "ཀ", 34: "ཁ", 35: "ག", 36: "ང", 37: "ཅ", 38: "ཆ"}

    assert _gid_map_corroborated(existing, db_map)


def test_thin_evidence_does_not_reject() -> None:
    # Below the conflict floor we have no basis to overrule the name match.
    existing = {13: "༈", 16: "་"}
    db_map = {13: "༉", 16: "༌"}

    assert len(existing) < MIN_GID_CONFLICTS
    assert _gid_map_corroborated(existing, db_map)


def test_gap_filling_alone_never_rejects() -> None:
    # Codes absent from the existing map carry no evidence either way.
    existing: dict[int, str] = {}
    db_map = {345: "༝", 359: "ཀ", 364: "ཁ", 381: "ག", 399: "པ"}

    assert _gid_map_corroborated(existing, db_map)


def test_only_referenced_codes_are_weighed() -> None:
    # Contradictions on GIDs the page never draws must not condemn the map.
    existing = {13: "༈", 16: "་", 18: "།", 782: "རྟ", 40: "ས"}
    db_map = {13: "༉", 16: "༌", 18: "༏", 782: "", 40: "སྒྲུབ"}

    assert not _gid_map_corroborated(existing, db_map, referenced={13, 16, 18, 782})
    assert _gid_map_corroborated(existing, db_map, referenced={40})


def _write_gid_lookup(path: Path, font_id: str, mapping: dict[int, str]) -> None:
    path.joinpath(f"{font_id}.json").write_text(
        json.dumps(
            {
                "_meta": {"lookup_kind": "gid"},
                font_id: {str(gid): text for gid, text in mapping.items()},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_same_name_candidates_prefer_existing_tounicode_compatibility(
    tmp_path: Path,
) -> None:
    # The first release is richer and covers every referenced GID, but its
    # glyph order contradicts the PDF. The smaller second release agrees.
    _write_gid_lookup(
        tmp_path,
        "richer-wrong",
        {1: "ཅ", 2: "ཆ", 3: "ཇ", 4: "ཉ", 5: "ཏ", 6: "ཐ"},
    )
    compatible = {1: "ཀ", 2: "ཁ", 3: "ག", 4: "ང"}
    _write_gid_lookup(tmp_path, "compatible", compatible)

    db_map, font_id, kind, matched_name = _pick_db_map_via_index(
        None,  # Type0 GID lookup does not inspect the document.
        1,
        is_type0=True,
        tier="gid",
        lookup_dir=tmp_path,
        name_index={"ps": {"sharedname": ["richer-wrong", "compatible"]}},
        pdf_norm_candidates=["sharedname"],
        referenced={1, 2, 3, 4},
        existing=dict(compatible),
    )

    assert font_id == "compatible"
    assert db_map == compatible
    assert kind == "gid"
    assert matched_name == "sharedname"


def test_same_name_candidates_keep_coverage_fallback_without_tibetan_evidence(
    tmp_path: Path,
) -> None:
    richer = {1: "ཀ", 2: "ཁ", 3: "ག"}
    _write_gid_lookup(tmp_path, "richer", richer)
    _write_gid_lookup(tmp_path, "smaller", {1: "ཀ", 2: "ཁ"})

    _db_map, font_id, _kind, _matched_name = _pick_db_map_via_index(
        None,
        1,
        is_type0=True,
        tier="gid",
        lookup_dir=tmp_path,
        name_index={"ps": {"sharedname": ["smaller", "richer"]}},
        pdf_norm_candidates=["sharedname"],
        referenced={1, 2, 3},
        existing={1: "A"},
    )

    assert font_id == "richer"


def test_real_monlam4_page_selects_compatible_same_name_release() -> None:
    doc = fitz.open(MONLAM4_PAGE)
    try:
        assert len(doc) == 1
        records, _stats = collect_font_merges(
            doc,
            lookup_dir=FONT_LOOKUP_DIR,
            tier="gid",
        )
        monlam4 = next(
            record
            for record in records
            if "MonlamUniOuChan4" in record["pdf_font_name"]
        )

        assert monlam4["db_key_matched"] == "def9c283d4d5abed"
        # GID 194 is the consonant glyph; GID 206 paints the separate gigu.
        assert monlam4["existing"][194] == "འི"
        assert monlam4["merged"][194] == "འ"
        assert monlam4["merged"][206] == "ི"

        apply_font_merges_to_doc(doc, records)
        repaired = extract_all(doc, ignore_actualtext=True)
    finally:
        doc.close()

    assert "ཀུན་བཟང་བླ་མའི་ཞལ་ལུང་" in repaired
    assert "འིི" not in repaired
