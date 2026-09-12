# Maintainer scripts (not installed with the wheel)

Run these from the **repository root** unless a script's docstring says otherwise.

| Folder | Tier / role | Typical entrypoints |
|--------|-------------|---------------------|
| [`gid/`](gid/) | **1 — GID → Unicode** | `build_per_font_gid_maps.py`, `update_font_lookup.py` |
| [`font_lookup_common/`](font_lookup_common/) | **Shared library** (no CLI) | `gid_map.py`, `per_font_maps.py`, `font_lookup_payload.py`, `single_font_lookup.py`, `pua_gname_rewriter.py`, `pua_gshape_patcher.py`, `pua_gid_patcher.py`, … — imported by gid / gname / gshape / pua |
| [`gname/`](gname/) | **2 — glyph name → Unicode** | `build_per_font_gname_maps.py`, `update_font_lookup.py` |
| [`gshape/`](gshape/) | **3 — outline hash → Unicode** | `build_per_font_gshape_maps.py`, `update_font_lookup.py` |
| [`pua/`](pua/) | **PUA-free lookup builders** (gname / gshape / gid) | `gname/build_pua_free_gname_maps.py`, `gshape/build_pua_free_gshape_maps.py`, `gid/build_pua_free_gid_maps.py`, `run_all.py`, `inventory.py`, `verify.py` |
| [`misc/`](misc/) | **Samples / one-offs / diagnostics** | `run_local_gshape_jomolhari_pipeline.py`, `inspect_pua_gname.py`, `patch_gid_lookup_from_gname_json.py`, `diagnose_contextual_gsub.py`, `edit_legacy_font_table.py` (generates a throwaway table-editor UI under `misc/out/`) |
| [`docs/`](docs/) | **Examples CLI matrix** | `run_examples_all_tiers.ps1` — six tiers × `docs/examples/` PDFs → `cli-results/` |

Font ZIPs live at **`fonts/<name>.zip`** by default; the same filenames under **`scripts/`** are used if missing from `fonts/` (both are gitignored). See the root [README](../README.md#font-lookup-workflows) for rebuild commands (e.g. `python scripts/gid/build_per_font_gid_maps.py …`).
