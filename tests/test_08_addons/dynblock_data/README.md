# Dynamic-block fixture data (not in this repository)

The tests in `tests/test_08_addons/test_85*.py` compare the `ezdxf.addons.dynblock`
and `ezdxf.addons.annoscale` output with CAD-made drawings. That data comes from a
private drawing template and is NOT published here. Without it, those tests skip
with the reason "CAD fixture data not in this checkout".

To run them, copy these files into this folder (they are git-ignored, so they
cannot be committed by accident):

| File | What it is | SHA-1 |
|---|---|---|
| `annotation_blocks_dyn.dxf` | dynamic block library (NOTES, SCHEDULE, SECTION MARKER, ...) with seeded representation data | `9b50e66c88adda8d9e701177aba19b4462dfe141` |
| `masonry_blocks_dyn.dxf` | masonry block library (lookup parameter) | `dccd87c339170267920e4427d1ea1603d886993d` |
| `break_line_blocks_dyn.dxf` | Break Line block library | `7809babc9f58d16292d344b6db6ebaa845f5032f` |
| `autocad_saved_dynamic_instances.dxf` | AutoCAD-saved drawing, 8 placed SECTION MARKER instances | `328eba8b8b578302b59d11ad80cc85471348d02a` |
| `section_marker_truth.json` | 7 AutoCAD SECTION MARKER instances, entity by entity | `6dfff0f02a2f556b27b1a44e9c1b871b8819417d` |
| `schedule_truth.json` | 16 AutoCAD SCHEDULE ROW / HEADER states | `eaf38bb41bf6dae551d9d96741e3ccb967b93e3d` |
| `detail_label_truth.json` | 3 AutoCAD DETAIL LABEL instances | `94649c89e575f3c5eb4729fc7efd5d400dc4d503` |
| `detail_label_autocad_list.txt` | AutoCAD LT LIST / RESETBLOCK console output | `f9c8e07b29ca03b40ce3e8690f04910049d76bf9` |

Optional: `reinforced_masonry_block.dxf`, the CAD source drawing of the masonry block
with its six CAD-placed instances (enables the masonry ground-truth tests).

The `build_*_truth.py` scripts in this folder record the truth JSON files from the
original template drawing.
