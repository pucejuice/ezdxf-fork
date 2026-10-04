# Fork changes against ezdxf 1.4.3

Base: tag `v1.4.3` (df5ef277f), MIT licence (`LICENSE`). Branches, each building on
the previous one:

| Branch | Work package | Content |
|---|---|---|
| `wp0-baseline` | WP0 | licence metadata fix |
| `wp4-importer-linetypes` | WP4 | Importer / linetype defects |
| `wp1-3-dynblock` | WP1, WP2, WP3 | `ezdxf.addons.dynblock`, `BlockLayout.dynamic`, complex linetypes in transplants |
| `wp5-delete-safety` | WP5 | deleting dynamic blocks and their representations |
| `wp6-annotation-scale` | WP6 | `ezdxf.addons.annoscale` (experimental) |
| `fork-integration` | all | the above plus this file |

## CAD fixture data

The CAD-made fixtures (block libraries, the AutoCAD-saved drawing, the truth JSON
files) come from a private drawing template and are not in this repository. They
are git-ignored in `tests/test_08_addons/dynblock_data`; its `README.md` lists the
files with their SHA-1. Without them the dynamic-block tests skip; with them, all
ground-truth tests run (SECTION MARKER 7 / 7, SCHEDULE 16 / 16, DETAIL LABEL 3 / 3).

## Reference function -> fork location

The downstream code can become a thin wrapper: import from the fork and keep only
its house rules (layers, colours, default entries, scale lists).

### Dynamic blocks (`dynamic_blocks.py` -> `ezdxf.addons.dynblock`)

Every public function keeps its name, signature and behaviour; differences are
listed below.

| Reference | Fork |
|---|---|
| `define_dynamic_blocks`, `_transplant`, `_collect`, `_external_handle`, `_ensure_*` | `ezdxf.addons.dynblock` (same names) |
| `graph_nodes`, `read_graph`, `read_linear_parameters`, `read_linear_stretch`, `read_visibility_states` | same |
| `_parse_param`, `_parse_action`, `_parse_stretch`, `_parse_lookup`, `_parse_eval_graph`, `_toposort` | same (private) |
| `add_dynamic`, `read_dynamic_state`, `add_stretched`, `_evaluate`, `_state_tags`, `_new_repdata` | same |
| `_stretch_wipeout`, `_follow_boundary`, `_boundary_refs`, `_stretch_unlisted`, `_move_hatch_seed` | same (private) |
| `set_multiline_attrib`, `attrib_text`, `mtext_props_for`, `wrapped_line_count`, `clone_entity` | same |
| `DynamicBlockError`, `SUPPORTED_NODE_TYPES`, `REPDATA_KEY` | same |
| (new) | `BlockLayout.dynamic` -> `DynamicBlockDefinition` or `None`; `dynblock.dynamic_definition()` |
| `LIBRARY_PATH` (package-relative default) | `dynblock.LIBRARY_PATH = None`: pass `library=` or set it once |

### Importer / linetype workarounds

| Reference | Fork |
|---|---|
| `block_library._repoint_linetype_styles` | not needed: `Importer.finalize()` maps every group-340 handle and leaves existing target linetypes alone |
| `cad_standardise._fix_complex_linetypes` | not needed for imports: `Importer` and `ezdxf.xref` remap every 340 (`LinetypePattern.get_style_handles()` / `map_style_handles()`) |
| `house_style._lin_tags` | still needed for `U=` (see open gaps); `A=` now works through `linetypes.add(pattern=...)` |
| `layer.dxf.linetype = ...` | `layer.linetype = ...` also works (`Layer.linetype` property) |

### Annotation scale (`annotations.py`, `viewports.py` -> `ezdxf.addons.annoscale`)

| Reference | Fork |
|---|---|
| `ensure_annotation_scale` | `annoscale.ensure_annotation_scale` (+ `find_annotation_scale`) |
| `make_annotative(entity, scales, default_scale, standard=...)` | `annoscale.make_annotative(entity, scales, default_scale)`: TEXT only |
| `annotation_scale_name`, `viewport_scale_name`, `set_viewport_annotation_scale` | `annoscale` (same names) |

## Where the fork differs from the reference, and why

1. **`_ensure_layer`** no longer calls the project's layer creator. It copies the
   library layer's colour (made positive, so the layer is ON), linetype, plot flag,
   lineweight, true colour and XDATA (incl. transparency). Layer flags (frozen /
   locked) are not copied, as before.
2. **No default library.** `LIBRARY_PATH` is `None`; calls without `library=` raise
   `DynamicBlockError` naming the fix.
3. **Representation key**: the vendor-prefixed root-dictionary key of the original
   libraries -> `DYNBLOCK_REPDATA`. Rebuild the libraries with the new key, or rename
   the root-dictionary entry.
4. **Complex linetypes are transplanted** (the reference raised): the library's
   pattern tags are copied unchanged, every group-340 handle is re-pointed by name
   (text style) or by .shx file (shape file); an unresolvable handle raises.
5. **Handle code set** is taken from ezdxf (`lldxf.types.HEX_HANDLE_CODES`, identical
   to the reference's set).
6. **Deleting a dynamic block** (WP5): `delete_block(safe=True)` refuses a block with
   `*U` representations; `safe=False` turns the representations into plain
   anonymous blocks first.
7. **`make_annotative`**: `ACDB_ANNOTATIONSCALES` without the hard-owner flag, CLASS
   flags 1153, scales found by name and keyed `A<n>` (all as the CAD-made fixtures
   show); MTEXT raises (no CAD reference); scale names are checked by syntax only
   (the house `cannoscales` list belongs to the wrapper).
8. **`_fmt_ratio`** was not in the supplied extract; the fork formats a ratio with up
   to 6 decimals (`20.0` -> `'20'`, `2.5` -> `'2.5'`).

## ezdxf behaviour changed outside the add-ons

- `Importer.update_complex_linetypes()` only touches linetypes created by the importer.
- `Importer.handle_mapping` also maps discarded (already existing) table entries.
- `Importer.import_block(name, rename=False)` returns the name as stored in the target
  (block names are case-insensitive).
- `Layer.linetype` is a property.
- The `.lin` compiler honours `A=` (74 bit 1) and raises on unknown parameters.
- `Dictionary.destroy()` also deletes entries it owns (330) without the 280 flag.
- `DXFTagStorage.destroy()` destroys objects it hard-owns by group 360.
- `audit()` removes dynamic-block representation data pointing at a missing block
  record (`AuditError.INVALID_DYNAMIC_BLOCK_REPRESENTATION`).
