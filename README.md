# pdf-cmap-fix

Fix incorrect or incomplete PDF `/ToUnicode` CMaps so that text extraction, search, and copy-paste return correct Unicode.

Primary use case: **Tibetan stacked syllables** (Monlam, Himalaya, Jomolhari fonts) in Type0 / CID / Identity-H PDFs, and **Type1 / TrueType simple-encoding** PDFs (e.g. YesheDe) since v0.4.  
**GitHub:** [OpenPecha/pdf-cmap-fix](https://github.com/OpenPecha/pdf-cmap-fix)

---

## How it works

1. **Build a GID→Unicode map offline** from the source font's `cmap` table and GSUB ligature rules. Each stacked syllable glyph is decomposed back to its Unicode components. Maps are stored as `pdf_cmap_fix/data/font_lookup/<key>.json` (~970 fonts shipped with the package).
2. **Match the PDF font** by normalising the embedded font name to a lookup key, then merge the correct Unicode strings into the font's `/ToUnicode` stream.
3. **HuaGuang / Founder books** (`JBHGZWHZ.B6`, Distiller CJK ToUnicode, …) are decoded through UTFC's `Hg2Uni.tbl` instead of the GID/shape path (those lookups have no Tibetan and false-positive as Ededris).
4. **Extract or patch.** The patched text matches what renders on screen.

---

## Install

Requires Python 3.8+.

```bash
pip install git+https://github.com/OpenPecha/pdf-cmap-fix.git
```

Development install:

```bash
git clone https://github.com/OpenPecha/pdf-cmap-fix.git
cd pdf-cmap-fix
python -m venv .venv
. .venv/Scripts/activate     # Windows PowerShell
# . .venv/bin/activate        # macOS / Linux
pip install -e ".[dev]"
pytest -q
```

---

## CLI quick start

```bash
# Extract text: writes document.raw.txt, document.patched.txt, document.diff.txt
pdf-cmap-fix document.pdf

# Write a patched PDF
pdf-cmap-fix -p document.pdf

# Dump the merged ToUnicode maps as JSON (no rewrite)
pdf-cmap-fix --dump-cmap document.cmap-dump.json document.pdf

# Batch
pdf-cmap-fix doc1.pdf doc2.pdf doc3.pdf

# Use a custom font lookup directory (your own or a CI artifact)
pdf-cmap-fix --font-lookup-dir path/to/font_lookup document.pdf
```

Windows note: prefix with `$env:PYTHONUTF8 = "1"` if the console raises encoding errors.

### CLI by lookup tier

There are **three CLI entrypoints** (not one command with a tier flag). Each reads only JSON files whose `_meta.lookup_kind` matches that tier. Pass **`--font-lookup-dir`** to use a custom tree (built under `scripts/` or your own path); omit the flag to use the default directory for that CLI.

| Lookup kind | CLI | Default `--font-lookup-dir` | Inner JSON keys |
|-------------|-----|----------------------------|-----------------|
| **gid** (tier 1) | `pdf-cmap-fix` | `pdf_cmap_fix/data/font_lookup/` | GID decimal strings |
| **gname** (tier 2) | `pdf-cmap-fix-gname` | `pdf_cmap_fix/data/font_lookup_gname/` | PostScript glyph names |
| **gshape** (tier 3) | `pdf-cmap-fix-gshape` | `pdf_cmap_fix/data/font_lookup_gshape/` | Outline fingerprints |
| **gid PUA-free** | `pdf-cmap-fix` | `pdf_cmap_fix/data/font_lookup_gid_pua_free/` | Same as gid (PUA values patched) |
| **gname PUA-free** | `pdf-cmap-fix-gname` | `pdf_cmap_fix/data/font_lookup_gname_pua_free/` | Same as gname |
| **gshape PUA-free** | `pdf-cmap-fix-gshape` | `pdf_cmap_fix/data/font_lookup_gshape_pua_free/` | Same as gshape |

PUA-free directories are **not** shipped in the wheel by default; build them with [§ PUA-free variants](#pua-free-variants-optional) below.

**Tier 1 — GID** (`pdf-cmap-fix`):

```bash
pdf-cmap-fix document.pdf
pdf-cmap-fix --font-lookup-dir path/to/font_lookup document.pdf
pdf-cmap-fix --font-lookup-dir path/to/font_lookup -p document.pdf
pdf-cmap-fix --font-lookup-dir path/to/font_lookup --dump-cmap out.json document.pdf
```

**Tier 2 — glyph name** (`pdf-cmap-fix-gname`):

```bash
pdf-cmap-fix-gname document.pdf
pdf-cmap-fix-gname --font-lookup-dir path/to/font_lookup_gname document.pdf
pdf-cmap-fix-gname --font-lookup-dir path/to/font_lookup_gname -p document.pdf
pdf-cmap-fix-gname --font-lookup-dir path/to/font_lookup_gname --dump-cmap out.json document.pdf
```

**Tier 3 — outline shape** (`pdf-cmap-fix-gshape`):

```bash
pdf-cmap-fix-gshape document.pdf
pdf-cmap-fix-gshape --font-lookup-dir path/to/font_lookup_gshape document.pdf
pdf-cmap-fix-gshape --font-lookup-dir path/to/font_lookup_gshape -p document.pdf
pdf-cmap-fix-gshape --font-lookup-dir path/to/font_lookup_gshape --dump-cmap out.json document.pdf
```

**PUA-free** (same CLIs as above, different directory):

```bash
pdf-cmap-fix --font-lookup-dir path/to/font_lookup_gid_pua_free document.pdf
pdf-cmap-fix-gname --font-lookup-dir path/to/font_lookup_gname_pua_free document.pdf
pdf-cmap-fix-gshape --font-lookup-dir path/to/font_lookup_gshape_pua_free document.pdf
```

Any directory of `<key>.json` files produced by `scripts/gid/update_font_lookup.py`, `scripts/gname/update_font_lookup.py`, `scripts/gshape/update_font_lookup.py`, or the matching `scripts/pua/*/update_*.py` tools can be passed as `--font-lookup-dir`. JSON schema and `_meta` fields: [docs/font-lookup-tiers-2-3.md](docs/font-lookup-tiers-2-3.md).

---

## Python API

Tier 1 is exposed from the top-level package; tiers 2 and 3 use the same functions on their extractor modules:

```python
from pdf_cmap_fix import extract_pdf_text, patch_pdf, build_tounicode_dict

# Tier 1 (gid) — default: pdf_cmap_fix/data/font_lookup/
result = extract_pdf_text("document.pdf")
patch_pdf("document.pdf")
cmap = build_tounicode_dict("document.pdf")
result = extract_pdf_text("document.pdf", font_lookup_dir="path/to/font_lookup")

# Tier 2 (gname) — default: pdf_cmap_fix/data/font_lookup_gname/
from pdf_cmap_fix.gname.extractor import extract_pdf_text as extract_gname
from pdf_cmap_fix.gname.extractor import patch_pdf as patch_gname

extract_gname("document.pdf", font_lookup_dir="path/to/font_lookup_gname")

# Tier 3 (gshape) — default: pdf_cmap_fix/data/font_lookup_gshape/
from pdf_cmap_fix.gshape.extractor import extract_pdf_text as extract_gshape

extract_gshape("document.pdf", font_lookup_dir="path/to/font_lookup_gshape")

# PUA-free: same functions, point font_lookup_dir at the *_pua_free/ tree
extract_pdf_text("document.pdf", font_lookup_dir="path/to/font_lookup_gid_pua_free")
extract_gname("document.pdf", font_lookup_dir="path/to/font_lookup_gname_pua_free")
extract_gshape("document.pdf", font_lookup_dir="path/to/font_lookup_gshape_pua_free")
```

---

## Font lookup tiers

The default tier (GID) works for most PDFs. Try higher tiers when a font is not in the bundled data or when GIDs do not align.

| Tier | Inner key | Default data directory | Run with | Rebuild CLI |
|------|-----------|------------------------|----------|-------------|
| **1** (default) | GID as decimal string | `pdf_cmap_fix/data/font_lookup/` | `pdf-cmap-fix` | `scripts/gid/build_per_font_gid_maps.py` |
| **2** | PostScript glyph name | `pdf_cmap_fix/data/font_lookup_gname/` | `pdf-cmap-fix-gname` | `scripts/gname/build_per_font_gname_maps.py` |
| **3** | Outline fingerprint | `pdf_cmap_fix/data/font_lookup_gshape/` | `pdf-cmap-fix-gshape` | `scripts/gshape/build_per_font_gshape_maps.py` |

Each tier also has a **PUA-free sibling** (`font_lookup_gname_pua_free/`, etc.) produced by `scripts/pua/`. Use the **same CLI** as the base tier with `--font-lookup-dir` pointing at the PUA-free directory (see [CLI by lookup tier](#cli-by-lookup-tier) above).

See [docs/font-lookup-tiers-2-3.md](docs/font-lookup-tiers-2-3.md) and [docs/tiers/README.md](docs/tiers/README.md) for full details.

---

## Rebuilding font lookup data

### Bulk rebuild from ZIP archives

Place archives under `fonts/` at the repo root (gitignored). Later archives win on duplicate normalised font keys:

```bash
# Tier 1 (GID)
python scripts/gid/build_per_font_gid_maps.py \
    --zip fonts/bodyig.zip \
    --zip fonts/tibetan-fonts-main.zip \
    --zip fonts/tibetan-fonts-private-main.zip

# Tier 2 (glyph name)
python scripts/gname/build_per_font_gname_maps.py \
    --zip fonts/bodyig.zip \
    --zip fonts/tibetan-fonts-main.zip

# Tier 3 (outline hash)
python scripts/gshape/build_per_font_gshape_maps.py \
    --zip fonts/bodyig.zip \
    --zip fonts/tibetan-fonts-main.zip
```

Outputs: `pdf_cmap_fix/data/font_lookup/`, `font_lookup_gname/`, `font_lookup_gshape/` — one `<key>.json` per font face plus `_manifest.json`.

### Single font update

```bash
# Tier 1 (default output: pdf_cmap_fix/data/font_lookup/)
python scripts/gid/update_font_lookup.py path/to/font.ttf

# Tier 2
python scripts/gname/update_font_lookup.py path/to/font.ttf

# Tier 3
python scripts/gshape/update_font_lookup.py path/to/font.ttf

# Force key / custom directory / dry-run (all tiers)
python scripts/gid/update_font_lookup.py --key microsofthimalaya --dry-run path/to/font.ttf
```

### PUA-free variants (optional)

Build sibling trees with PUA values replaced by standard Unicode:

```bash
# Step 1: gname PUA-free (required before gshape / gid)
python scripts/pua/gname/build_pua_free_gname_maps.py \
    --zip fonts/bodyig.zip --zip fonts/tibetan-fonts-main.zip

# Step 2a: gshape PUA-free
python scripts/pua/gshape/build_pua_free_gshape_maps.py \
    --zip fonts/bodyig.zip \
    --gname-dir pdf_cmap_fix/data/font_lookup_gname_pua_free

# Step 2b: gid PUA-free (optional)
python scripts/pua/gid/build_pua_free_gid_maps.py \
    --zip fonts/bodyig.zip \
    --gname-dir pdf_cmap_fix/data/font_lookup_gname_pua_free

# Or run all steps at once
python scripts/pua/run_all.py --with-gid
```

See [docs/workflows/pua-free-font-lookups.md](docs/workflows/pua-free-font-lookups.md) for the full workflow.

---

## Worked examples

Reference PDFs live in [docs/examples/](docs/examples/). Each example includes **CLI-RUNS.md** and **cli-results/** showing the same PDF processed with all six bundled lookup trees (`font_lookup`, `font_lookup_gname`, `font_lookup_gshape`, and PUA-free siblings). Re-run: `.\scripts\docs\run_examples_all_tiers.ps1` from the repo root.

| Example | Producer | Pages | Font |
|---------|----------|-------|------|
| TI1055-01-001 | MS Word | 528 | Monlam Uni OuChan |
| TI1751-01-001 | InDesign | 528 | Monlam / Himalaya |
| TI803-01-001 | MS Word | 398 | Microsoft Himalaya |
| TI1461-01-001 | InDesign | 1 | Qomolangma + Monlam |
| TI1763-01-002 | MS Word | 1 | Monlam Uni OuChan 2 |
| sample | Mixed | — | Jomolhari + Cambria |

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/tiers/README.md](docs/tiers/README.md) | Tier map: data directories, script folders, PUA-free siblings |
| [docs/font-lookup-tiers-2-3.md](docs/font-lookup-tiers-2-3.md) | Tier 2 and 3 schema, `_meta` fields, CLI flags, merge behaviour |
| [docs/approach.md](docs/approach.md) | Technical deep-dive: GSUB walk, GID decomposition, ToUnicode patching |
| [docs/glossary-and-json.md](docs/glossary-and-json.md) | Terms (Type0, GID, GSUB, CMap …) and all JSON shapes |
| [docs/font-inventory.md](docs/font-inventory.md) | All ~970 normalised font keys shipped in `font_lookup/` |
| [docs/workflows/pua-free-font-lookups.md](docs/workflows/pua-free-font-lookups.md) | PUA-free batch pipeline with diagrams |
| [docs/workflows/local-jomolhari-gshape-pua-free.md](docs/workflows/local-jomolhari-gshape-pua-free.md) | Windows smoke path: local Jomolhari + Cambria gshape |
| [docs/examples/](docs/examples/) | Reference PDFs and extraction outputs |

---

## Project structure

```text
pdf_cmap_fix/                     installable package
  __init__.py                     public API: extract_pdf_text, patch_pdf, …
  __main__.py                     python -m pdf_cmap_fix  (tier 1)
  tounicode_core.py               shared ToUnicode merge + tier filter
  glyph_fingerprint.py            HashPointPen outline hashing (tier 3)
  gid/extractor.py                tier 1 CLI + API
  gname/extractor.py              tier 2 CLI + API
  gshape/extractor.py             tier 3 CLI + API
  data/
    font_lookup/                  tier 1 — shipped in wheel  (~970 JSON files)
    font_lookup_gname/            tier 2 — rebuild from ZIPs
    font_lookup_gshape/           tier 3 — rebuild from ZIPs
    font_lookup_gname_pua_free/   optional PUA-free sibling (gitignored)
    font_lookup_gshape_pua_free/  optional PUA-free sibling (gitignored)
    font_lookup_gid_pua_free/     optional PUA-free sibling (gitignored)

scripts/
  font_lookup_common/             shared library imported by all tier CLIs
    gid_map.py                    GSUB walk + GID decomposition
    per_font_maps.py              bulk ZIP/dir builder
    single_font_lookup.py         single-font updater
    font_lookup_payload.py        payload builder (gid / gname / gshape)
    font_sources.py               ZIP + directory font iterator
    pua_gname_rewriter.py         PUA → Unicode via uni* glyph names
    pua_gshape_patcher.py         gshape PUA patch via fingerprint→gname
    pua_gid_patcher.py            GID PUA patch via glyph order + gname
    pua_utils.py                  PUA detection helpers
    font_archive_index.py         ZIP key index builder
  gid/                            tier 1 CLIs
    build_per_font_gid_maps.py    bulk ZIP/dir → font_lookup/
    update_font_lookup.py         single font → one JSON
  gname/                          tier 2 CLIs
    build_per_font_gname_maps.py
    update_font_lookup.py
  gshape/                         tier 3 CLIs
    build_per_font_gshape_maps.py
    update_font_lookup.py
  pua/                            PUA-free builders
    gname/build_pua_free_gname_maps.py   bulk → font_lookup_gname_pua_free/
    gname/update_pua_free_gname.py       single font
    gshape/build_pua_free_gshape_maps.py bulk + --gname-dir
    gshape/update_pua_free_gshape.py     single font + --gname-json
    gid/build_pua_free_gid_maps.py       bulk + --gname-dir
    gid/update_pua_free_gid.py           single font + --gname-json
    inventory.py                         scan lookup trees for PUA
    verify.py                            exit 1 if any PUA remains
    run_all.py                           orchestrator (all tiers in order)
  misc/
    inspect_pua_gname.py          interactive PUA map analysis
    patch_gid_lookup_from_gname_json.py  patch GID JSON from gname sidecar
    diagnose_contextual_gsub.py   GSUB type 5/6/8 diagnostic
    run_local_gshape_jomolhari_pipeline.py  Windows Jomolhari smoke pipeline

fonts/                            gitignored ZIP archives
  bodyig.zip
  tibetan-fonts-main.zip
  tibetan-fonts-private-main.zip

docs/
  README.md                       documentation index
  approach.md                     technical deep-dive
  glossary-and-json.md
  font-lookup-tiers-2-3.md
  font-inventory.md
  tiers/
    README.md                     tier map quick reference
    data-layout.md                data directory legend
  workflows/
    pua-free-font-lookups.md      global PUA-free batch
    local-jomolhari-gshape-pua-free.md  Windows smoke path
  examples/
    TI1055-01-001/                MS Word 528-page example
    TI1751-01-001/                InDesign 528-page example
    TI803-01-001/                 MS Word, Microsoft Himalaya
    TI1461-01-001/                InDesign, Qomolangma + Monlam
    TI1763-01-002/                MS Word, Monlam Uni OuChan 2
    sample/                       Jomolhari + Cambria sample

tests/
pyproject.toml
```

---

## Limits

- **Type0 / CID / Identity-H**: fully supported on all three tiers
  (`gid`, `gname`, `gshape`). The historical primary target.
- **Type1 / MMType1 / TrueType (simple, 1-byte)**: supported on
  `gname` and `gshape` (tier 2 and tier 3) since v0.4. Char codes go
  through the font's `/Encoding` (predefined base + `/Differences`) to
  a PostScript glyph name; the lookup is done on the glyph name (or
  its outline fingerprint, tier 3). Output ToUnicode uses a 1-byte
  codespace (`<00> <FF>`). Tier 1 (`gid`) is intentionally **not**
  supported for simple fonts: GIDs in their embedded font programs are
  font-local and not portable across PDFs.
- **Type3** (procedural): not supported. These fonts have no
  embedded font program, so neither glyph name nor outline fingerprint
  lookups have anything to bind against. Silently skipped.
- **TrueType simple-encoding without `/Encoding`**: the embedded
  font's cmap is now read as the built-in encoding. Quartz / Affinity
  subsets that keep ``uniXXXX`` glyph names are repaired from those
  names even when the face is not in the lookup DB. Ghostscript
  outputs whose cmap keys are sequential integers *unrelated* to
  glyph names still need a gname / gshape lookup for that font.

See [docs/approach.md](docs/approach.md) for the full rationale.

---

## License

MIT
