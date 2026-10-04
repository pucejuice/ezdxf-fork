"""Read, copy and write AutoCAD/BricsCAD DYNAMIC blocks.

Ported from a downstream drafting library; the evidence quoted below refers to
the CAD-made fixtures in ``tests/test_08_addons/dynblock_data``.

Why this module exists
----------------------
A structural drawing template (``STR_TEMPLATE_AUG_2026.dwt``) holds 158 named
blocks, 134 of them dynamic.  :func:`ezdxf.addons.Importer.import_block` copies a
block's entities but SILENTLY DROPS its dynamic definition - the block record's
``ACAD_ENHANCEDBLOCK`` evaluation graph (parameters, grips, actions) - so NOTES,
SCHEDULE ROW, SECTION LABEL ... arrive static.  Never use ``Importer`` for a
dynamic block.

:func:`define_dynamic_blocks` instead transplants each block's entities AND its
whole block-record extension-dictionary subtree (evaluation graph, sort table,
purge preventer) into an existing document, giving every object a fresh handle
and remapping every handle-valued tag (owners, reactors, 330/331/332 graph
references, 1005 xdata handles, ...).  Handles pointing outside the block
(annotation scales, table entries) are resolved BY NAME in the target; an
unresolvable handle raises :class:`DynamicBlockError` rather than being left
dangling.

:func:`add_dynamic` then writes an instance in a chosen dynamic STATE -
rotation, flip, visibility state and linear (stretch) values - in pure DXF,
the way CAD represents it: an anonymous ``*U`` block (copy of the definition
with the block's actions applied) tagged ``AcDbBlockRepBTag`` back to the
parent, and an INSERT extension dictionary ``AcDbBlockRepresentation``
(``AcDbRepData`` + ``AppDataCache/ACAD_ENHANCEDBLOCKDATA``, one XRECORD per
graph node holding its state).  :func:`read_graph` reads the parameters,
actions and their selections generically and derives the evaluation ORDER from
the graph's own edges; the actions are applied in that order: a stretch moves
its selection's vertices, a rotate action turns its selection about the
rotation parameter's base, a flip action mirrors its selection about the flip
line, visibility sets the DXF invisible flag (group 60) of the hidden entities
- all definition entities are kept, as CAD keeps them.  The per-node
representation constants come from the block library (captured from one
CAD-made seed instance per block by the library builder);
only the state values are rewritten.  :func:`add_stretched` (one linear value)
is a wrapper over it.

Evidence: the 7 SECTION MARKER instances AutoCAD placed in the template
(rotations 0 / -90 / 155.3 / 245.3 / 52.6 deg, flipped and not, 'Marker Only' /
'Marker and Tail', a stretched tail) are reproduced entity by entity to 1e-12 mm
and their XRECORD state values exactly (``tests/test_08_addons/test_851_dynblock_section_marker.py``,
fixture ``tests/test_08_addons/dynblock_data/section_marker_truth.json``).  The
linear writer was proven by the Director 2026-09-24 (BricsCAD V25 reads NOTES as
``EffectiveName`` NOTES, ``IsDynamicBlock`` True, grip-edits it; AutoCAD LT 2027
round-trips it).

Scope and limits (all raise, none silently degrade)
---------------------------------------------------
* The target drawing must be DXF R2018+ (``ezdxf.new("R2018")``): the
  multi-line NOTES ATTDEF is an embedded-MTEXT attribute, which ezdxf (and
  BricsCAD's R2013 save, measured) exports as a single-line attribute below
  R2018.
* Node types evaluated: :data:`SUPPORTED_NODE_TYPES` (linear / stretch,
  rotation / rotate, flip / flip action, visibility, lookup parameter / lookup
  action, their grips).  A block with any other node type (XY, polar, point,
  base point, alignment, array, move, scale ...) raises ``NotImplementedError``
  naming them, before anything is written.
* Lookup: the table is read from the BLOCKLOOKUPACTION generically; an entry
  (``add_dynamic(..., lookup="150 Full")``) sets the visibility state and
  distances its row carries, and is written as the lookup parameter's state.
  The table is applied to the targets BEFORE evaluation, so the lookup action's
  two-way graph edges (a cycle) take no part in the order.  Columns on linear and
  visibility parameters only; chained lookups raise.  Evidence: the owner's
  reinforced masonry block (``masonry_blocks_dyn.dxf``) - its six CAD-made instances are
  reproduced entity by entity (``tests/test_08_addons/test_854_dynblock_masonry.py``).
* Mirroring (flip) covers LINE, LWPOLYLINE, CIRCLE, ARC, POINT, solid HATCH and
  INSERT - written as CAD writes them (extrusion kept +Z, bulges negated, arc
  edges re-oriented, INSERT with negative X scale + 180 deg).  TEXT / ATTDEF /
  MTEXT in a flip selection raise: CAD keeps mirrored text readable (MIRRTEXT)
  and there is no CAD-made reference for it here.
* Stretch moves the vertices listed in the action's stretch selection (group
  331 + vertex indices).  An entity in the action's selection SET (group 330)
  with no listed vertex is moved whole, and an associative HATCH there follows
  its stretched boundary (seed points inside the frame move) - as AutoCAD writes
  the template's Column Width 19.5 SCHEDULE ROW / HEADER instances (the MARK
  text moves with the left edge; ``tests/test_08_addons/test_852_dynblock_schedule.py``).
  BricsCAD's seed ``*U`` leaves that ATTDEF and hatch in place but moves the
  instance's MARK attribute the same way.  EVERY associative hatch whose boundary
  object is stretched follows it, selected or not, tracked by boundary VERTEX
  (identity, not position - a stretch can fold one vertex onto another); a
  HATCH listed in the vertex list moves its seed point (vertex i = seed i) and,
  with seed 0, its pattern origin.
* The XRECORD header pair (group 70) is the library seed's - BricsCAD's
  (33, 1); AutoCAD writes (25, 104).  Both CADs read either (the linear writer
  with BricsCAD constants round-trips through AutoCAD LT).

Units: drawing units (mm).  Status: experimental

Known gap: the per-node representation constants (the XRECORD contents written
for each graph node) are NOT computed.  They are captured from one CAD-made seed
instance per block and stored in the block library under :data:`REPDATA_KEY`;
only the state values are rewritten.  A block with no seed cannot be placed.
"""

from __future__ import annotations

import io
import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from ezdxf.lldxf.types import HEX_HANDLE_CODES

logger = logging.getLogger(__name__)

__all__ = [
    "LIBRARY_PATH",
    "LIBRARY_BLOCKS",
    "REPDATA_KEY",
    "SUPPORTED_NODE_TYPES",
    "DynamicBlockError",
    "define_dynamic_blocks",
    "graph_nodes",
    "read_graph",
    "read_linear_parameters",
    "read_linear_stretch",
    "read_visibility_states",
    "add_dynamic",
    "read_dynamic_state",
    "add_stretched",
    "set_multiline_attrib",
    "attrib_text",
    "mtext_props_for",
    "wrapped_line_count",
    "clone_entity",
    "DynamicBlockDefinition",
    "dynamic_definition",
    "survey",
    "survey_summary",
]

#: Default block library (DXF path or Drawing) used when a function is called
#: without ``library=``.  ezdxf ships no library: set this, or pass ``library=``.
LIBRARY_PATH: Optional[Path] = None

#: The annotation / notes family of the reference library
#: ``annotation_blocks_dyn.dxf`` (A$C7F3A7EFF is the anonymous-named block nested
#: in SECTION MARKER and comes across with it).
LIBRARY_BLOCKS = (
    "NOTES", "SCHEDULE HEADER", "SCHEDULE ROW", "NORTH POINT", "SECTION MARKER",
    "A$C7F3A7EFF", "SECTION LABEL", "DETAIL MARKER", "DETAIL LABEL",
    "PLAN LABEL", "TITLE LABEL",
)

#: Root-dictionary key in the library under which the per-block representation
#: constants live: ``REPDATA_KEY / <block name> / <node id>`` -> XRECORD holding the
#: ACAD_ENHANCEDBLOCKDATA tags of the CAD seed instance, plus ``__REPDATA__``
#: holding the AcDbRepData flag (group 70).  This is a convention of this module,
#: not a CAD structure: libraries built by the original downstream code used a
#: vendor-prefixed key and must be rebuilt (or the key renamed) to use this one.
REPDATA_KEY = "DYNBLOCK_REPDATA"
_REPDATA_FLAG = "__REPDATA__"

_R2018 = "AC1032"

# Group codes whose value is a handle.  320-369 are the pointer / owner codes
# (330-339 soft pointer - includes the graph's 331/332 - 340-349 hard pointer,
# 350-359 soft owner, 360-369 hard owner), 390-399 hard pointer (plot style),
# 480/481 hard pointers, 105 the DIMSTYLE handle, 1005 an XDATA handle.
# Checked against ezdxf: this is exactly ezdxf.lldxf.types.HEX_HANDLE_CODES.  ezdxf
# itself does not translate 320-329 on INSERT / XREF (TRANSLATABLE_POINTER_CODES);
# none occurs in the reference libraries' transplanted objects.
_HANDLE_CODES = frozenset(HEX_HANDLE_CODES)
_OWNED_CODES = frozenset(range(350, 370))

_GRAPH_NODE_PREFIXES = ("BLOCK", "ACAD_EVAL")


class DynamicBlockError(RuntimeError):
    """A dynamic block could not be transplanted or written faithfully."""


# ── library ───────────────────────────────────────────────────────────


@lru_cache(maxsize=4)
def _load_library_cached(path: str, mtime: float):
    import ezdxf

    return ezdxf.readfile(path)


def _library(library) -> Any:
    """The library document (a path is read once and cached; a Drawing is used as-is)."""
    if library is None:
        library = LIBRARY_PATH
    if library is None:
        raise DynamicBlockError(
            "no dynamic block library: pass library= (a DXF path or Drawing) or set "
            "ezdxf.addons.dynblock.LIBRARY_PATH")
    if hasattr(library, "entitydb"):
        return library
    p = Path(library)
    if not p.is_file():
        raise FileNotFoundError(f"dynamic block library not found: {p}")
    return _load_library_cached(str(p.resolve()), p.stat().st_mtime)


def _require_r2018(doc, what: str) -> None:
    if doc.dxfversion < _R2018:
        raise ValueError(
            f"{what} needs a DXF R2018+ drawing (AC1032); this one is {doc.dxfversion}. "
            "Create it with ezdxf.new('R2018') - below R2018 the embedded-MTEXT "
            "(multi-line) attributes are exported as single-line ones."
        )


# ── tag helpers ───────────────────────────────────────────────────────


def _tags(obj) -> list:
    """All tags of a DXFTagStorage object (graph nodes are not modelled by ezdxf)."""
    return [t for sub in obj.xtags.subclasses for t in sub]


def _export_pairs(entity, dxfversion: str) -> list[tuple[int, str]]:
    """The entity exactly as ezdxf would write it: a list of (group code, raw value)."""
    from ezdxf.lldxf.tagwriter import TagWriter

    s = io.StringIO()
    entity.export_dxf(TagWriter(s, dxfversion=dxfversion))
    lines = s.getvalue().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) % 2:
        raise DynamicBlockError(f"odd DXF tag stream exporting {entity}")
    return [(int(lines[i]), lines[i + 1]) for i in range(0, len(lines), 2)]


def graph_nodes(doc, block_record) -> list:
    """Every node of a block record's ``ACAD_ENHANCEDBLOCK`` evaluation graph (graph first)."""
    xd = block_record.get_extension_dict() if block_record.has_extension_dict else None
    if xd is None or "ACAD_ENHANCEDBLOCK" not in xd:
        return []
    seen, stack, out = set(), [xd["ACAD_ENHANCEDBLOCK"]], []
    while stack:
        o = stack.pop()
        if o is None or o.dxf.handle in seen:
            continue
        seen.add(o.dxf.handle)
        out.append(o)
        for t in _tags(o):
            if t.code in (340, 350, 360) and isinstance(t.value, str):
                n = doc.entitydb.get(t.value)
                if n is not None and n.dxftype().startswith(_GRAPH_NODE_PREFIXES):
                    stack.append(n)
    return out


# ── define: transplant with handle remap ─────────────────────────────


def _collect(src, block) -> dict:
    """Source objects to transplant for *block*: its entities, their extension
    dictionaries, and the block record's extension-dictionary subtree - closed
    over ownership (codes 350-369).  Returns ``{handle: entity}`` in a stable order."""
    db = src.entitydb
    br = block.block_record
    order: dict = {}
    todo = list(block)
    if br.has_extension_dict:
        todo.append(br.get_extension_dict().dictionary)
    todo.reverse()
    while todo:
        e = todo.pop()
        h = e.dxf.handle
        if h in order:
            continue
        if e.dxftype() == "INSERT" and e.attribs:
            raise DynamicBlockError(
                f"block {block.name!r}: nested INSERT {h} carries ATTRIBs - not supported")
        order[h] = e
        for code, value in _export_pairs(e, src.dxfversion):
            if code in _OWNED_CODES:
                child = db.get(value.strip())
                if child is not None and child.dxf.handle not in order:
                    todo.append(child)
    return order


def _nested_names(block) -> list[str]:
    return [e.dxf.name for e in block if e.dxftype() == "INSERT"]


def _ensure_appid(doc, name: str) -> None:
    if name not in doc.appids:
        doc.appids.add(name)


def _copy_xdata(src_entry, dst_entry, doc) -> None:
    if not src_entry.xdata:
        return
    for app, tags in src_entry.xdata.data.items():
        _ensure_appid(doc, app)
        dst_entry.set_xdata(app, [(t.code, t.value) for t in list(tags)[1:]])


def _ensure_linetype(src, doc, name: str) -> None:
    """A linetype used by a library block is copied into the target if absent
    (the masonry block's C-block hidden lines are DASHED); a target that already
    has the name keeps its own.  A complex linetype (text / shape elements) is
    copied with its own pattern tags; every group-340 style handle is re-pointed
    by name (:func:`_complex_linetype_style`)."""
    if not name or name.upper() in ("BYLAYER", "BYBLOCK", "CONTINUOUS") or name in doc.linetypes:
        return
    s = src.linetypes.get(name)
    if s is None:
        raise DynamicBlockError(f"linetype {name!r} is used by a library block but is not in the library")
    pt = s.pattern_tags
    if pt.is_complex_type():
        from ezdxf.entities.ltype import LinetypePattern
        from ezdxf.lldxf.tags import Tags

        new = doc.linetypes.add(name, [0.0], description=s.dxf.get("description", ""))
        new.pattern_tags = LinetypePattern(Tags(pt.tags))
        new.pattern_tags.map_style_handles(lambda h: _complex_linetype_style(src, doc, name, h))
        return
    total = next((t.value for t in pt.tags if t.code == 40), 0.0)
    elements = [t.value for t in pt.tags if t.code == 49]
    doc.linetypes.add(name, [total, *elements], description=s.dxf.get("description", ""))


#: LAYER properties copied from the library into the target.  The layer is
#: created ON (absolute colour) and its flags (frozen / locked) are not copied;
#: transparency travels with the XDATA (AcCmTransparency).
_LAYER_ATTRIBS = ("linetype", "plot", "lineweight", "true_color")


def _complex_linetype_style(src, doc, linetype: str, handle: str) -> str:
    """Target handle for group-340 style *handle* of complex *linetype*: a named
    text style maps to the same-named style (copied if absent), a nameless
    shape-file entry to the target's entry for the same .shx (created if absent).
    An unresolvable handle raises :class:`DynamicBlockError`."""
    style = src.entitydb.get(handle)
    if style is None or style.dxftype() != "STYLE":
        raise DynamicBlockError(f"complex linetype {linetype!r}: style handle {handle} resolves to "
                                f"{style.dxftype() if style is not None else 'nothing'} in the library")
    style_name = style.dxf.get("name", "")
    if style_name:
        _ensure_style(src, doc, style_name)
        return doc.styles.get(style_name).dxf.handle
    font = style.dxf.get("font", "")
    if not font:
        raise DynamicBlockError(f"complex linetype {linetype!r}: style {handle} has neither a name "
                                "nor a shape file")
    return doc.styles.get_shx(font).dxf.handle


def _ensure_layer(src, doc, name: str) -> None:
    """A layer used by a library block is copied into the target if absent - a
    plain copy of the library layer's properties and XDATA; a target that already
    has the name keeps its own."""
    if name in doc.layers:
        return
    s = src.layers.get(name)
    _ensure_linetype(src, doc, s.dxf.get("linetype", "Continuous"))
    new = doc.layers.add(name, color=abs(s.dxf.get("color", 7)))
    for k in _LAYER_ATTRIBS:
        if s.dxf.hasattr(k):
            new.dxf.set(k, s.dxf.get(k))
    _copy_xdata(s, new, doc)


def _ensure_style(src, doc, name: str) -> None:
    if not name or name in doc.styles:
        return
    s = src.styles.get(name)
    attribs = {k: s.dxf.get(k) for k in ("flags", "height", "width", "oblique", "generation_flags",
                                         "last_height", "bigfont") if s.dxf.hasattr(k)}
    new = doc.styles.add(name, font=s.dxf.get("font", ""), dxfattribs=attribs)
    _copy_xdata(s, new, doc)


def _ensure_resources(src, doc, objs: Iterable) -> None:
    from ezdxf.entities import AttDef, is_graphic_entity

    for e in objs:
        if not is_graphic_entity(e):
            continue
        dxf = e.dxf
        if dxf.hasattr("layer"):
            _ensure_layer(src, doc, dxf.layer)
        if dxf.hasattr("linetype"):
            _ensure_linetype(src, doc, dxf.linetype)
        if e.dxftype() in ("TEXT", "ATTDEF", "ATTRIB", "MTEXT") and dxf.hasattr("style"):
            _ensure_style(src, doc, dxf.style)
        if isinstance(e, AttDef) and e.has_embedded_mtext_entity:
            vm = e.virtual_mtext_entity()
            if vm.dxf.hasattr("style"):
                _ensure_style(src, doc, vm.dxf.style)
        if e.xdata:
            for app in e.xdata.data:
                _ensure_appid(doc, app)


def _scale_name(scale) -> Optional[str]:
    """SCALE objects are not modelled by ezdxf 1.4: the name is group 300."""
    return next((t.value for t in _tags(scale) if t.code == 300), None)


def _ensure_scale(src, doc, scale) -> str:
    """Handle of the target's annotation scale named like *scale*; copied into the
    target's ACAD_SCALELIST if absent (``ezdxf.new`` starts with an empty list)."""
    from ezdxf.entities import factory
    from ezdxf.lldxf.extendedtags import ExtendedTags

    name = _scale_name(scale)
    sl = doc.rootdict.get_required_dict("ACAD_SCALELIST")
    for _, s in sl.items():
        if _scale_name(s) == name:
            return s.dxf.handle
    new_h = doc.entitydb.next_handle()
    out = []
    for code, value in _export_pairs(scale, src.dxfversion):
        v = value.strip()
        if code == 5:
            value = new_h
        elif code in _HANDLE_CODES and v not in ("0", ""):
            if v != scale.dxf.owner:
                raise DynamicBlockError(f"SCALE {name!r} references {v}; not copied")
            value = sl.dxf.handle
        out.append(f"{code}\n{value}")
    new = factory.load(ExtendedTags.from_text("\n".join(out) + "\n"), doc)
    doc.entitydb.add(new)
    new.post_load_hook(doc)
    doc.objects.add_object(new)
    i = len(sl)
    while f"A{i}" in sl:
        i += 1
    sl.add(f"A{i}", new)
    return new_h


def _external_handle(src, doc, handle: str, where: str) -> str:
    """Map a handle that points OUTSIDE the transplanted set to the target, by name."""
    o = src.entitydb.get(handle)
    if o is None:
        raise DynamicBlockError(f"{where}: handle {handle} resolves to nothing in the library")
    t = o.dxftype()
    if t == "SCALE":
        return _ensure_scale(src, doc, o)
    tables = {"LAYER": doc.layers, "STYLE": doc.styles, "LTYPE": doc.linetypes,
              "DIMSTYLE": doc.dimstyles, "BLOCK_RECORD": doc.block_records}
    if t in tables and o.dxf.name in tables[t]:
        return tables[t].get(o.dxf.name).dxf.handle
    raise DynamicBlockError(f"{where}: handle {handle} -> {t} {getattr(o.dxf, 'name', '')!r} has no target")


def _copy_classes(src, doc, dxftypes: set) -> None:
    from ezdxf.entities.dxfclass import DXFClass

    have = {c.dxf.name for c in doc.classes}
    for c in src.classes:
        if c.dxf.name in dxftypes and c.dxf.name not in have:
            n = DXFClass.new(doc=doc)
            n.update_dxf_attribs({k: c.dxf.get(k) for k in (
                "name", "cpp_class_name", "app_name", "flags", "was_a_proxy", "is_an_entity")})
            doc.classes.register(n)
            have.add(c.dxf.name)


def _transplant(src, doc, name: str) -> None:
    from ezdxf.entities import factory, is_graphic_entity
    from ezdxf.entities.xdict import ExtensionDict
    from ezdxf.lldxf.extendedtags import ExtendedTags

    sblock = src.blocks.get(name)
    sbr = sblock.block_record
    objs = _collect(src, sblock)
    _ensure_resources(src, doc, objs.values())

    blk_ent = sblock.block
    dblock = doc.blocks.new(
        name,
        base_point=blk_ent.dxf.base_point,
        dxfattribs={k: blk_ent.dxf.get(k) for k in ("flags", "description") if blk_ent.dxf.hasattr(k)},
    )
    dbr = dblock.block_record
    for k in ("units", "explode", "scale"):
        if sbr.dxf.hasattr(k):
            dbr.dxf.set(k, sbr.dxf.get(k))
    _copy_xdata(sbr, dbr, doc)

    mapping = {sbr.dxf.handle: dbr.dxf.handle}
    for h in objs:
        mapping[h] = doc.entitydb.next_handle()

    loaded = []
    for h, e in objs.items():
        pairs = _export_pairs(e, src.dxfversion)
        out = []
        for i, (code, value) in enumerate(pairs):
            v = value.strip()
            if code in _HANDLE_CODES and v not in ("0", ""):
                if v in mapping:
                    value = mapping[v]
                elif code == 5 and i > 0:
                    pass  # a SORTENTSTABLE sort key that is not an entity handle
                else:
                    value = _external_handle(src, doc, v, f"{name}/{e.dxftype()} {h} code {code}")
            elif code == 1001:
                _ensure_appid(doc, v)
            out.append(f"{code}\n{value}")
        new = factory.load(ExtendedTags.from_text("\n".join(out) + "\n"), doc)
        doc.entitydb.add(new)
        loaded.append((e, new))

    # 2nd loading stage, as ezdxf does after reading a file: resolve handles -> objects
    post = []
    for _, new in loaded:
        cmd = new.post_load_hook(doc)
        if cmd is not None:
            post.append(cmd)
    for cmd in post:
        cmd()

    for old, new in loaded:
        if old.dxf.owner == sbr.dxf.handle and is_graphic_entity(old):
            dblock.add_entity(new)
        else:
            doc.objects.add_object(new)
    if sbr.has_extension_dict:
        dbr.extension_dict = ExtensionDict(doc.entitydb[mapping[sbr.get_extension_dict().dictionary.dxf.handle]])
    _copy_classes(src, doc, {new.dxftype() for _, new in loaded})


def define_dynamic_blocks(doc, names: Optional[Iterable[str]] = None, library=None) -> list[str]:
    """Define library blocks in *doc* WITH their dynamic evaluation graph.

    Args:
        doc: an existing ezdxf Drawing, DXF R2018+ (e.g. ``ezdxf.new("R2018")``
            then ``create_sheet``).
        names: block names to bring in; default every named block in the
            library.  Nested blocks (SECTION MARKER -> A$C7F3A7EFF) come too.
        library: library DXF path or Drawing; default :data:`LIBRARY_PATH`.

    Returns:
        The names actually defined by this call (dependencies first).  A name
        already defined in *doc* is left untouched (idempotent).

    Raises:
        ValueError: *doc* below R2018.
        KeyError: a name not in the library.
        DynamicBlockError: a handle could not be remapped faithfully.
    """
    _require_r2018(doc, "define_dynamic_blocks")
    src = _library(library)
    if names is None:
        names = [b.name for b in src.blocks if not b.name.startswith("*")]
    order: list[str] = []

    def visit(n: str, stack: tuple = ()) -> None:
        if n in order or n in stack:
            return
        if n not in src.blocks:
            raise KeyError(f"block {n!r} is not in the dynamic block library")
        for child in _nested_names(src.blocks.get(n)):
            visit(child, stack + (n,))
        order.append(n)

    for n in names:
        visit(n)
    done = []
    for n in order:
        if n in doc.blocks:
            continue
        _transplant(src, doc, n)
        done.append(n)
    return done


# ── reading the graph ─────────────────────────────────────────────────

#: Graph node types :func:`add_dynamic` evaluates.  Grips and grip-location
#: components only place the interactive grips; they carry no geometry.
SUPPORTED_NODE_TYPES = frozenset({
    "ACAD_EVALUATION_GRAPH", "BLOCKGRIPLOCATIONCOMPONENT",
    "BLOCKLINEARPARAMETER", "BLOCKLINEARGRIP", "BLOCKSTRETCHACTION",
    "BLOCKROTATIONPARAMETER", "BLOCKROTATIONGRIP", "BLOCKROTATEACTION",
    "BLOCKFLIPPARAMETER", "BLOCKFLIPGRIP", "BLOCKFLIPACTION",
    "BLOCKVISIBILITYPARAMETER", "BLOCKVISIBILITYGRIP",
    "BLOCKLOOKUPPARAMETER", "BLOCKLOOKUPGRIP", "BLOCKLOOKUPACTION",
})
_PARAM_KIND = {"BLOCKLINEARPARAMETER": "linear", "BLOCKROTATIONPARAMETER": "rotation",
               "BLOCKFLIPPARAMETER": "flip", "BLOCKVISIBILITYPARAMETER": "visibility",
               "BLOCKLOOKUPPARAMETER": "lookup"}
_PARAM_SUBCLASS = {"linear": "AcDbBlockLinearParameter", "rotation": "AcDbBlockRotationParameter",
                   "flip": "AcDbBlockFlipParameter"}
_ACTION_KIND = {"BLOCKSTRETCHACTION": "stretch", "BLOCKROTATEACTION": "rotate", "BLOCKFLIPACTION": "flip",
                "BLOCKLOOKUPACTION": "lookup"}
#: Parameter kinds a lookup table column can set (the lookup column itself is "lookup").
_LOOKUP_INPUT_KINDS = ("linear", "visibility")
# value-set flags (group 96 of a linear / rotation parameter), measured on the
# library: NOTES 5 = min 5.0 + increment 3.5, SCHEDULE HEADER 7 = min + max 30 + increment
_VS_MIN, _VS_MAX, _VS_INC = 1, 2, 4


def _node_id(tags) -> Optional[int]:
    return next((t.value for t in tags if t.code == 90), None)


def _sub(n, name: str) -> list:
    """Tags of subclass *name* of graph node *n*, without its 100 marker."""
    for sub in n.xtags.subclasses[1:]:
        if sub and sub[0].code == 100 and sub[0].value == name:
            return list(sub)[1:]
    raise DynamicBlockError(f"{n.dxftype()} {n.dxf.handle} has no {name} subclass")


class _Cursor:
    """Sequential reader of a node's tags that raises on an unexpected layout."""

    def __init__(self, tags: list, where: str):
        self.t, self.i, self.where = tags, 0, where

    def peek(self) -> Optional[int]:
        return self.t[self.i].code if self.i < len(self.t) else None

    def take(self, code: int):
        if self.peek() != code:
            raise DynamicBlockError(f"{self.where}: expected group {code} at tag {self.i}, "
                                    f"found {self.peek()}")
        v = self.t[self.i].value
        self.i += 1
        return v


def _fields(tags: list, codes: tuple, where: str) -> dict:
    """``{code: value}`` for a subclass of single-valued fields; each code must occur once."""
    out: dict = {}
    for x in tags:
        if x.code in codes:
            if x.code in out:
                raise DynamicBlockError(f"{where}: group {x.code} occurs twice")
            out[x.code] = x.value
    missing = [c for c in codes if c not in out]
    if missing:
        raise DynamicBlockError(f"{where}: groups {missing} missing")
    return out


def _parse_param(n) -> dict:
    """One parameter node: kind, label (group 305, else the node name 300), its
    points in definition coordinates and its kind-specific properties."""
    from ezdxf.math import Vec3

    typ = n.dxftype()
    kind = _PARAM_KIND[typ]
    t = _tags(n)
    name = next((x.value for x in t if x.code == 300), "")
    p: dict[str, Any] = {"id": _node_id(t), "type": typ, "kind": kind, "name": name,
         "label": next((x.value for x in t if x.code == 305 and x.value), None) or name}
    if kind == "visibility":
        one = _sub(n, "AcDbBlock1PtParameter")
        p["points"] = [Vec3(next(x.value for x in one if x.code == 1010))]
        own = _sub(n, "AcDbBlockVisibilityParameter")
        states: dict[str, list] = {}
        names: list[str] = []
        cur: Optional[list] = None
        for x in own:
            if x.code == 303:
                cur = states.setdefault(x.value, [])
                names.append(x.value)
            elif x.code == 332 and cur is not None:
                cur.append(x.value)
        current = next((x.value for x in own if x.code == 91), 0)
        p.update(states=states, state_names=names, governed=[x.value for x in own if x.code == 331],
                 default=names[current] if 0 <= current < len(names) else None)
        return p
    if kind == "lookup":
        # AcDbBlockLookUpParameter: 303 = property label ('Block'), 304 = description,
        # 94 = node id of the lookup action whose table it reads
        one = _sub(n, "AcDbBlock1PtParameter")
        p["points"] = [Vec3(next(x.value for x in one if x.code == 1010))]
        lk = _fields(_sub(n, "AcDbBlockLookUpParameter"), (303, 94), f"{typ} {n.dxf.handle}")
        p.update(label=lk[303] or name, action_id=lk[94], default=None)   # default: reverse lookup
        return p
    two = _sub(n, "AcDbBlock2PtParameter")
    base = Vec3(next(x.value for x in two if x.code == 1010))
    end = Vec3(next(x.value for x in two if x.code == 1011))
    p.update(points=[base, end], base=base, end=end)
    own = _sub(n, _PARAM_SUBCLASS[kind])
    if kind in ("linear", "rotation"):
        d = {x.code: x.value for x in own if x.code in (96, 141, 142, 143, 175)}
        p.update(flags=d.get(96, 0), min=d.get(141), max=d.get(142), inc=d.get(143),
                 base_location=d.get(175, 0))
    if kind == "linear":
        p["default"] = (end - base).magnitude
    elif kind == "rotation":
        ref = next((x.value for x in own if x.code == 1011), None)
        p["angle_point"] = Vec3(ref) if ref is not None else end
        p["points"].append(p["angle_point"])        # rotated with the parameter
        p["default"] = math.degrees((end - base).angle - (p["angle_point"] - base).angle) % 360.0
    else:   # flip
        flip_names = {x.code: x.value for x in own if x.code in (307, 308)}
        p["states"] = (flip_names.get(307, "Not flipped"), flip_names.get(308, "Flipped"))
        p["default"] = False
    return p


def _parse_action(n) -> dict:
    """One action node: kind, driving parameter, the parameters and entities it moves."""
    from ezdxf.math import Vec3

    typ = n.dxftype()
    where = f"{typ} {n.dxf.handle}"
    t = _tags(n)
    c = _Cursor(_sub(n, "AcDbBlockAction"), where)
    param_ids = [c.take(91) for _ in range(c.take(70))]
    selection = [c.take(330) for _ in range(c.take(71))]
    a = {"id": _node_id(t), "type": typ, "kind": _ACTION_KIND[typ], "name": next(
        (x.value for x in t if x.code == 300), ""), "param_ids": param_ids, "selection": selection,
        "param_points": []}
    if typ == "BLOCKSTRETCHACTION":
        a.update(_parse_stretch(n))
    elif typ == "BLOCKROTATEACTION":
        # fixed fields, read by code: AutoCAD writes 92 93 301 302, BricsCAD 92 301 93 302
        w = _fields(_sub(n, "AcDbBlockActionWithBasePt"), (92, 93, 1011, 280, 1012), where)
        xs, ys = w[92], w[93]
        offset, dependent, base_point = Vec3(w[1011]), bool(w[280]), Vec3(w[1012])
        drv = _fields(_sub(n, "AcDbBlockRotationAction"), (94,), where)[94]
        if dependent and (xs, ys) != (drv, drv):
            raise DynamicBlockError(f"{where}: base point follows parameters {xs}/{ys}, angle {drv}")
        a.update(param_id=drv, offset=offset, dependent=dependent, base_point=base_point)
    elif typ == "BLOCKLOOKUPACTION":
        a.update(_parse_lookup(n))
    else:   # BLOCKFLIPACTION
        f = _fields(_sub(n, "AcDbBlockFlipAction"), (92, 93, 94, 95), where)
        ids = {f[92], f[93], f[94], f[95]}
        if len(ids) != 1:
            raise DynamicBlockError(f"{where}: driven by several parameters {sorted(ids)}")
        a["param_id"] = ids.pop()
    return a


def _parse_stretch(n) -> dict:
    """BLOCKSTRETCHACTION: driving parameter id (92/93) and which of its points
    drives (301 ``EndXDelta`` / ``BaseXDelta``), frame (1011), the stretch
    selection - 73 = entity count, then per entity 331 handle, 74 = index count,
    that many 94 vertex indices - then the parameters it moves: 75 = count, per
    parameter 95 id, 76 = point count, that many 94 point indices (0 base, 1 end),
    then 140 distance multiplier and 141 angle offset."""
    where = f"BLOCKSTRETCHACTION {n.dxf.handle}"
    tags = _sub(n, "AcDbBlockStretchAction")
    k = next((i for i, x in enumerate(tags) if x.code == 72), None)
    if k is None:
        raise DynamicBlockError(f"{where}: no stretch frame (group 72)")
    head = _fields(tags[:k], (92, 93, 301, 302), where)   # order differs AutoCAD / BricsCAD
    xs, xname, ys, yname = head[92], head[301], head[93], head[302]
    c = _Cursor(tags, where)
    c.i = k
    if xs != ys:
        raise NotImplementedError(f"{where}: stretch driven by two parameters ({xs}, {yname}) - "
                                  "an XY-parameter stretch is not implemented")
    point = {"EndXDelta": 1, "BaseXDelta": 0}.get(xname)
    if point is None:
        raise NotImplementedError(f"{where}: stretch keyed to {xname!r} is not implemented")
    frame = [c.take(1011) for _ in range(c.take(72))]
    sel = []
    for _ in range(c.take(73)):
        h = c.take(331)
        sel.append([h, [c.take(94) for _ in range(c.take(74))]])
    pp = []
    if c.peek() == 75:
        for _ in range(c.take(75)):
            pid = c.take(95)
            pp.append([pid, [c.take(94) for _ in range(c.take(76))]])
    rest = {x.code: x.value for x in c.t[c.i:]}
    t = _tags(n)
    return {"id": _node_id(t), "param_id": xs, "point": point, "frame": frame, "select": sel,
            "param_points": pp, "multiplier": rest.get(140, 1.0), "angle_offset": rest.get(141, 0.0)}


def _parse_lookup(n) -> dict:
    """BLOCKLOOKUPACTION: the lookup TABLE, as raw text cells.

    Measured on the owner's reinforced masonry block (2026-10-02): ``92`` row count,
    ``93`` column count, a ``301`` (empty), then rows x columns ``302`` cells
    ROW-MAJOR; then per column ``303`` (empty), ``94`` the parameter node id it
    reads / sets, ``95`` the value type (40 distance, 1 string), ``96``, ``282``,
    ``305`` the label shown when no row matches ('Custom'), ``281``, ``304``
    the connected property ('UpdatedDistance' / 'VisibilityState' /
    'lookupString'); then ``280``.  The cells are typed in :func:`read_graph`
    once the columns' parameters are known.  ``param_id`` (the driving lookup
    parameter) is filled in there too: it is the lookup parameter whose group 94
    names this action."""
    where = f"BLOCKLOOKUPACTION {n.dxf.handle}"
    c = _Cursor(_sub(n, "AcDbBlockLookupAction"), where)
    nrows, ncols = c.take(92), c.take(93)
    c.take(301)
    cells = [c.take(302) for _ in range(nrows * ncols)]
    columns = []
    for _ in range(ncols):
        c.take(303)
        pid, vtype = c.take(94), c.take(95)
        c.take(96)
        c.take(282)
        unmatched = c.take(305)
        c.take(281)
        columns.append({"param_id": pid, "value_type": vtype, "unmatched": unmatched, "property": c.take(304)})
    if c.peek() == 280:
        c.take(280)
    if c.peek() is not None:
        raise DynamicBlockError(f"{where}: unexpected group {c.peek()} after the lookup columns")
    rows = [cells[i * ncols:(i + 1) * ncols] for i in range(nrows)]
    return {"param_id": None, "columns": columns, "rows": rows}


def _type_lookup(graph: dict, a: dict) -> None:
    """Bind lookup action *a* to its lookup parameter and type its cells by column kind
    (linear -> float, visibility -> a state of the parameter, lookup -> str)."""
    name, params = graph["name"], graph["params"]
    owners = [pid for pid, p in params.items() if p["kind"] == "lookup" and p["action_id"] == a["id"]]
    if len(owners) != 1:
        raise DynamicBlockError(f"block {name!r}: lookup action {a['id']} is read by {len(owners)} "
                                "lookup parameters (expected 1)")
    a["param_id"] = owners[0]
    kinds = []
    for col in a["columns"]:
        p = params.get(col["param_id"])
        if p is None:
            raise DynamicBlockError(f"block {name!r}: lookup column names node {col['param_id']}, "
                                    "which is not a parameter of the graph")
        if p["kind"] == "lookup" and col["param_id"] != a["param_id"]:
            raise NotImplementedError(f"block {name!r}: lookup column {p['label']!r} is another lookup "
                                      "parameter (chained lookups are not implemented)")
        if p["kind"] not in _LOOKUP_INPUT_KINDS + ("lookup",):
            raise NotImplementedError(f"block {name!r}: a lookup column on a {p['kind']} parameter "
                                      f"({p['label']!r}) is not implemented")
        kinds.append(p)
    if sum(1 for p in kinds if p["kind"] == "lookup") != 1:
        raise DynamicBlockError(f"block {name!r}: lookup action {a['id']} has no column for its own "
                                "lookup parameter")
    typed = []
    for r, row in enumerate(a["rows"]):
        out = []
        for p, cell in zip(kinds, row):
            if p["kind"] == "linear":
                try:
                    cell = float(cell)
                except ValueError:
                    raise DynamicBlockError(f"block {name!r}: lookup row {r} {p['label']!r} cell {cell!r} "
                                            "is not a number") from None
            elif p["kind"] == "visibility" and cell not in p["states"]:
                raise DynamicBlockError(f"block {name!r}: lookup row {r} names visibility state {cell!r}, "
                                        f"not one of {p['state_names']}")
            out.append(cell)
        typed.append(out)
    a["rows"] = typed
    a["lookup_col"] = next(i for i, p in enumerate(kinds) if p["kind"] == "lookup")


def _lookup_row(graph: dict, lookup_pid: int, entry: str) -> tuple[dict, list]:
    """``(lookup action, row)`` of *entry* in the table read by lookup parameter *lookup_pid*."""
    a = next(x for x in graph["actions"].values() if x["kind"] == "lookup" and x["param_id"] == lookup_pid)
    col = a["lookup_col"]
    for row in a["rows"]:
        if row[col] == entry:
            return a, row
    p = graph["params"][lookup_pid]
    raise ValueError(f"block {graph['name']!r} has no {p['label']!r} lookup entry {entry!r} "
                     f"(entries {[r[col] for r in a['rows']]})")


def _reverse_lookup(graph: dict, values: dict) -> None:
    """Fill every lookup parameter that has no value from the other parameters' values:
    the entry of the FIRST row whose every input cell equals the current value
    (distances within 1e-6), else the lookup column's no-match label (group 305,
    'Custom').  This is how CAD shows a lookup property for values set by other
    grips - inferred, not yet checked against a CAD-made 'Custom' instance (the
    six in the owner's source drawing all name a row that matches their values)."""
    for a in graph["actions"].values():
        if a["kind"] != "lookup" or values.get(a["param_id"]) is not None:
            continue
        col = a["lookup_col"]
        found = None
        for row in a["rows"]:
            ok = True
            for i, (c, cell) in enumerate(zip(a["columns"], row)):
                if i == col:
                    continue
                v = values.get(c["param_id"])
                if isinstance(cell, float) and v is not None and not isinstance(v, str):
                    ok = abs(float(v) - cell) <= 1e-6
                else:
                    ok = v == cell
                if not ok:
                    break
            if ok:
                found = row[col]
                break
        values[a["param_id"]] = found if found is not None else a["columns"][col]["unmatched"]


def _parse_eval_graph(g) -> tuple[list, list]:
    """ACAD_EVALUATION_GRAPH -> ([(node index, node id, handle)], [(from id, to id)]).

    Node records are ``91 index, 93 flags, 95 node id, 360 handle, 92 x4``; edge
    records ``92 index, 93, 94, 91 from-node index, 91 to-node index, 92 x5``
    (measured on SECTION MARKER: rotation grip 57 -> parameter 56 -> rotate action 61)."""
    where = f"ACAD_EVALUATION_GRAPH {g.dxf.handle}"
    c = _Cursor(_sub(g, "AcDbEvalGraph"), where)
    while (code := c.peek()) in (96, 97):
        c.take(code)  # type: ignore[arg-type]
    nodes = []
    while c.peek() == 91:
        idx, _, nid, h = c.take(91), c.take(93), c.take(95), c.take(360)
        for _ in range(4):
            c.take(92)
        nodes.append((idx, nid, h))
    by_index = {idx: nid for idx, nid, _ in nodes}
    edges = []
    while c.peek() is not None:
        c.take(92)
        c.take(93)
        c.take(94)
        a, b = c.take(91), c.take(91)
        for _ in range(5):
            c.take(92)
        if a not in by_index or b not in by_index:
            raise DynamicBlockError(f"{where}: edge {a}->{b} names a node that is not in the graph")
        edges.append((by_index[a], by_index[b]))
    return nodes, edges


def _toposort(ids: list, succ: dict) -> list:
    """Kahn's algorithm; ties broken by the graph's own node order."""
    import heapq

    rank = {n: i for i, n in enumerate(ids)}
    indeg = dict.fromkeys(ids, 0)
    for a in ids:
        for b in succ.get(a, ()):
            if b not in indeg:
                raise DynamicBlockError(f"graph dependency {a}->{b} names an unknown node")
            indeg[b] += 1
    ready = [(rank[n], n) for n in ids if indeg[n] == 0]
    heapq.heapify(ready)
    out = []
    while ready:
        _, n = heapq.heappop(ready)
        out.append(n)
        for b in sorted(succ.get(n, ()), key=lambda x: rank[x]):
            indeg[b] -= 1
            if indeg[b] == 0:
                heapq.heappush(ready, (rank[b], b))
    if len(out) != len(ids):
        # chained parameters whose actions move each other (template FRAMING_SPEC,
        # REO_SPEC, M16 Anchor, Strip Footing): CAD breaks the loop with the
        # parameters' chain-action settings, which are not modelled here
        raise NotImplementedError(f"the actions form a dependency cycle through graph nodes "
                                  f"{sorted(set(ids) - set(out))}; evaluating it is not implemented")
    return out


def read_graph(doc, name: str) -> dict:
    """The dynamic behaviour of block *name*, read generically from its evaluation graph.

    Returns ``{"name", "unsupported", "params", "actions", "grips", "order",
    "types"}``:

    * ``unsupported`` - node types :func:`add_dynamic` cannot evaluate (XY,
      polar, point, array, lookup, move, scale, ... parameters/actions).  When
      non-empty, nothing else is parsed.
    * ``params`` / ``actions`` - ``{node id: dict}`` (see :func:`_parse_param`,
      :func:`_parse_action`): each action's driving parameter, the parameters it
      moves (group 91 of AcDbBlockAction; for a stretch the 75/95/76/94 point
      list) and its entity selection.
    * ``grips`` - ``{grip id: parameter id}`` from the graph's grip -> parameter edges.
    * ``order`` - parameter and action ids in EVALUATION order: a topological
      sort of the graph's own edges (grip -> parameter -> action) plus
      action -> parameter for every parameter an action moves (a rotate action
      that carries the linear parameters round must run before them).  SECTION
      MARKER: rotation 56, rotate 61, Label Arm 63, stretch 88, Arm Gap 70,
      stretch 87, Tail Arm 78, stretch 85, flip 90, flip action 95, visibility 116.
    """
    if name not in doc.blocks:
        raise KeyError(f"block {name!r} is not defined in the drawing")
    nodes = graph_nodes(doc, doc.blocks.get(name).block_record)
    types = sorted({n.dxftype() for n in nodes})
    g: dict[str, Any] = {"name": name, "unsupported": sorted(set(types) - SUPPORTED_NODE_TYPES), "params": {},
         "actions": {}, "grips": {}, "order": [], "types": types}
    if not nodes or g["unsupported"]:
        return g
    graph = next((n for n in nodes if n.dxftype() == "ACAD_EVALUATION_GRAPH"), None)
    if graph is None:
        raise DynamicBlockError(f"block {name!r}: no ACAD_EVALUATION_GRAPH")
    for n in nodes:
        if n.dxftype() in _PARAM_KIND:
            p = _parse_param(n)
            g["params"][p["id"]] = p
        elif n.dxftype() in _ACTION_KIND:
            a = _parse_action(n)
            g["actions"][a["id"]] = a
    for a in g["actions"].values():
        if a["kind"] == "lookup":
            _type_lookup(g, a)
    gnodes, edges = _parse_eval_graph(graph)
    type_of = {_node_id(_tags(n)): n.dxftype() for n in nodes if n.dxftype() != "ACAD_EVALUATION_GRAPH"}
    # a lookup action is joined to every column parameter BOTH ways (masonry block:
    # lookup 14 <-> D 1 / Display 6 / W 15 / Block 10), a cycle; the table is applied
    # to the targets before evaluation (_resolve_targets / _reverse_lookup), so the
    # lookup action takes no part in the geometric order
    lookups = {aid for aid, a in g["actions"].items() if a["kind"] == "lookup"}
    succ: dict = {}
    for a, b in edges:
        if a in lookups or b in lookups:
            continue
        succ.setdefault(a, set()).add(b)
        if type_of.get(a, "").endswith("GRIP") and b in g["params"]:
            g["grips"][a] = b
    for aid, a in g["actions"].items():
        if a["param_id"] not in g["params"]:
            raise DynamicBlockError(f"block {name!r}: action {aid} is driven by {a['param_id']}, "
                                    "which is not a parameter of the graph")
        for pid in a["param_ids"] + [q for q, _ in a["param_points"]]:
            if pid in g["params"]:
                succ.setdefault(aid, set()).add(pid)
    order = _toposort([nid for _, nid, _ in gnodes], succ)
    g["order"] = [n for n in order if n in g["params"] or n in g["actions"]]
    return g


def read_linear_parameters(doc, name: str) -> list[dict]:
    """Every LINEAR parameter of block *name* (``id``/``base``/``end``/``label``/``min``/``max``/``inc``)."""
    br = doc.blocks.get(name).block_record
    return [_parse_param(n) for n in graph_nodes(doc, br) if n.dxftype() == "BLOCKLINEARPARAMETER"]


def read_linear_stretch(doc, name: str, parameter: Optional[str] = None):
    """``(param, [stretch])`` for one linear parameter of block *name*, read from its graph.

    Args:
        parameter: the parameter's label (e.g. ``"Line Height"``); required when
            the block has more than one linear parameter (SCHEDULE ROW: Row
            Height + Column Width).

    Returns:
        ``param`` dict (see :func:`read_linear_parameters`) and the stretch
        actions it drives, each ``{"id", "param_id", "frame", "select": [[handle,
        [vertex indices]], ...], "param_points", ...}``.
    """
    if name not in doc.blocks:
        raise KeyError(f"block {name!r} is not defined in the drawing")
    br = doc.blocks.get(name).block_record
    nodes = graph_nodes(doc, br)
    params = [_parse_param(n) for n in nodes if n.dxftype() == "BLOCKLINEARPARAMETER"]
    if not params:
        raise DynamicBlockError(f"block {name!r} has no linear parameter")
    if parameter is None:
        if len(params) > 1:
            raise ValueError(f"block {name!r} has {len(params)} linear parameters "
                             f"{[p['label'] for p in params]}; pass parameter=")
        param = params[0]
    else:
        match = [p for p in params if p["label"] == parameter]
        if not match:
            raise ValueError(f"block {name!r} has no linear parameter {parameter!r} "
                             f"(has {[p['label'] for p in params]})")
        param = match[0]
    stretches = [s for s in (_parse_stretch(n) for n in nodes if n.dxftype() == "BLOCKSTRETCHACTION")
                 if s["param_id"] == param["id"]]
    return param, stretches


def read_visibility_states(doc, name: str) -> Optional[dict]:
    """The visibility parameter of block *name*, or None if it has none.

    Returns ``{"id", "label", "default", "governed", "states": {state: [visible
    entity handles]}}``.  States are group 303 in BLOCKVISIBILITYPARAMETER, each
    followed by a 94 count and that many 332 handles; the entity set the
    parameter governs is the 331 list.  The default is the state the definition
    is drawn in - the state INDEX in group 91 (SECTION MARKER: 0 = 'Marker and
    Tail', whose hidden set is exactly the definition's 6 invisible entities).
    Group 301 is NOT the state: it is a free label ('Display' on SECTION MARKER).
    """
    br = doc.blocks.get(name).block_record
    for n in graph_nodes(doc, br):
        if n.dxftype() == "BLOCKVISIBILITYPARAMETER":
            p = _parse_param(n)
            return {"id": p["id"], "label": p["name"], "default": p["default"],
                    "governed": p["governed"], "states": p["states"]}
    return None


class DynamicBlockDefinition:
    """The dynamic behaviour of one block definition, read from its
    ``ACAD_ENHANCEDBLOCK`` evaluation graph (see :func:`read_graph`).

    Returned by ``BlockLayout.dynamic`` (or :func:`dynamic_definition`) for a
    dynamic block; a static block returns ``None``.

    Attributes:
        name: block name
        node_types: sorted DXF types of every graph node
        unsupported: node types this module cannot evaluate; when not empty,
            :attr:`parameters`, :attr:`actions` and :attr:`order` are empty
        parameters: ``{node id: parameter dict}``
        actions: ``{node id: action dict}``
        grips: ``{grip node id: parameter node id}``
        order: parameter and action node ids in evaluation order, derived from
            the graph's own edges
    """

    def __init__(self, graph: dict):
        self._graph = graph
        self.name: str = graph["name"]
        self.node_types: list[str] = graph["types"]
        self.unsupported: list[str] = graph["unsupported"]
        self.parameters: dict = graph["params"]
        self.actions: dict = graph["actions"]
        self.grips: dict = graph["grips"]
        self.order: list[int] = graph["order"]

    @property
    def is_supported(self) -> bool:
        """``True`` if every graph node type can be evaluated."""
        return not self.unsupported

    def require_supported(self) -> None:
        """Raises :class:`NotImplementedError` naming the unsupported node types."""
        if self.unsupported:
            raise NotImplementedError(
                f"block {self.name!r} has dynamic node types {self.unsupported} that are "
                "not evaluated (supported: linear/stretch, rotation/rotate, flip, "
                "visibility, lookup)")

    def parameter(self, label: str) -> dict:
        """The parameter with `label` (or node name); raises :class:`KeyError`."""
        match = [p for p in self.parameters.values() if label in (p["label"], p["name"])]
        if len(match) != 1:
            raise KeyError(f"block {self.name!r} has {len(match)} parameters named {label!r}")
        return match[0]

    def __repr__(self) -> str:
        labels = ", ".join(f"{p['label']} ({p['kind']})" for p in self.parameters.values())
        extra = f", unsupported {self.unsupported}" if self.unsupported else ""
        return f"<DynamicBlockDefinition {self.name!r}: {labels}{extra}>"


def dynamic_definition(block_layout) -> Optional[DynamicBlockDefinition]:
    """The :class:`DynamicBlockDefinition` of `block_layout`, or ``None`` for a
    static block (no ``ACAD_ENHANCEDBLOCK`` evaluation graph)."""
    doc = block_layout.doc
    if not graph_nodes(doc, block_layout.block_record):
        return None
    return DynamicBlockDefinition(read_graph(doc, block_layout.name))


# ── transforms, the way CAD writes them ───────────────────────────────

#: Entity types a rotate action can carry round (ezdxf's own transform).
_ROTATABLE = frozenset({"LINE", "LWPOLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE", "POINT", "HATCH",
                        "TEXT", "ATTDEF", "MTEXT", "SOLID", "WIPEOUT", "INSERT"})
#: Entity types a flip action can mirror.  Text is not in it: CAD keeps mirrored
#: text readable (MIRRTEXT) and there is no CAD-made reference for that here.
_MIRRORABLE = frozenset({"LINE", "LWPOLYLINE", "CIRCLE", "ARC", "POINT", "HATCH", "INSERT"})


def _require_plan(e, what: str) -> None:
    from ezdxf.math import Vec3

    if not Vec3(e.dxf.get("extrusion", (0, 0, 1))).isclose((0, 0, 1)):
        raise NotImplementedError(f"{what} of a {e.dxftype()} with extrusion {e.dxf.extrusion} "
                                  "is not implemented")


def _xy_rotation(center, angle: float):
    from ezdxf.math import Matrix44

    return Matrix44.chain(Matrix44.translate(-center.x, -center.y, 0), Matrix44.z_rotate(angle),
                          Matrix44.translate(center.x, center.y, 0))


def _xy_mirror(p1, p2):
    from ezdxf.math import Matrix44

    th = (p2 - p1).angle
    return Matrix44.chain(Matrix44.translate(-p1.x, -p1.y, 0), Matrix44.z_rotate(-th),
                          Matrix44.scale(1, -1, 1), Matrix44.z_rotate(th), Matrix44.translate(p1.x, p1.y, 0))


def _transform_insert(e, m, mirror: bool) -> None:
    """INSERT: point transformed; a mirror is written as CAD writes it - NEGATIVE X
    scale and rotation + 180 (template *U1071: xscale -0.01, rotation 335.278 for
    a 155.278 deg arm), rotation normalised to [0, 360)."""
    from ezdxf.math import Vec3

    _require_plan(e, "rotate/flip")
    d = e.dxf
    d.insert = m.transform(Vec3(d.insert))
    r = math.radians(d.get("rotation", 0.0))
    v = m.transform_direction(Vec3(math.cos(r), math.sin(r), 0))
    if mirror:
        v = -v
        d.xscale = -d.get("xscale", 1.0)
    d.rotation = math.degrees(math.atan2(v.y, v.x)) % 360.0


def _mirror_hatch(h, m, line_angle: float) -> None:
    """Mirror a solid HATCH in its plane the way CAD does: extrusion kept at
    +Z, polyline bulges negated, arc edges reflected with the orientation flag
    flipped (template *U1071: a clockwise 225..315 edge becomes ccw 20.278..110.278)."""
    from ezdxf.entities.boundary_paths import ArcEdge, LineEdge, PolylinePath
    from ezdxf.math import Vec2, Vec3

    _require_plan(h, "flip")
    if not h.dxf.solid_fill:
        raise NotImplementedError("flip of a pattern-filled HATCH is not implemented")
    if h.gradient is not None and h.gradient.kind:   # 450 = 1; SECTION MARKER's hatch is 0 (solid)
        raise NotImplementedError("flip of a gradient HATCH is not implemented")

    def mx(p):
        return Vec2(m.transform(Vec3(p[0], p[1], 0)))

    two_th = 2.0 * math.degrees(line_angle)
    for p in h.paths:
        if isinstance(p, PolylinePath):
            p.vertices = [(*mx((x, y)), -b) for x, y, b in p.vertices]
            continue
        for g in p.edges:
            if isinstance(g, LineEdge):
                g.start, g.end = mx(g.start), mx(g.end)
            elif isinstance(g, ArcEdge):
                span = g.end_angle - g.start_angle
                g.center = mx(g.center)
                start = (two_th - g.end_angle) % 360.0
                g.start_angle, g.end_angle = start, start + span
                g.ccw = not g.ccw
            else:
                raise NotImplementedError(f"flip of a HATCH {type(g).__name__} is not implemented")
    h.seeds = [tuple(mx(s)) for s in h.seeds]   # tuples: ezdxf's writer slices them


def _transform_entity(e, m, mirror: bool, line_angle: float = 0.0) -> None:
    """Rotate (*mirror* False) or mirror one *U entity in the XY plane."""
    from ezdxf.math import Vec3

    t = e.dxftype()
    if t == "INSERT":
        _transform_insert(e, m, mirror)
        return
    if not mirror:
        if t not in _ROTATABLE:
            raise NotImplementedError(f"rotate action on a {t} is not implemented")
        if t == "HATCH":
            _require_plan(e, "rotate")
            seeds = [tuple(m.transform(Vec3(x, y, 0)).vec2) for x, y in e.seeds]
        e.transform(m)
        if t == "HATCH":
            # ezdxf 1.4 leaves the seed points where they were; CAD turns them with the
            # hatch (template *U119, -90 deg: seed (-29.29, 0) -> (0, 29.29))
            e.seeds = seeds
        if t in ("TEXT", "ATTDEF"):
            e.dxf.rotation = e.dxf.get("rotation", 0.0) % 360.0
        return
    if t not in _MIRRORABLE:
        raise NotImplementedError(f"flip action on a {t} is not implemented (no CAD-made reference)")
    if t == "HATCH":
        _mirror_hatch(e, m, line_angle)
        return
    _require_plan(e, "flip")
    d = e.dxf
    if t == "LINE":
        d.start, d.end = m.transform(Vec3(d.start)), m.transform(Vec3(d.end))
    elif t == "POINT":
        d.location = m.transform(Vec3(d.location))
    elif t == "CIRCLE":
        d.center = m.transform(Vec3(d.center))
    elif t == "ARC":
        c, s, en = m.transform(Vec3(d.center)), m.transform(e.start_point), m.transform(e.end_point)
        d.center = c
        d.start_angle = math.degrees((en - c).angle) % 360.0
        d.end_angle = math.degrees((s - c).angle) % 360.0
    elif t == "LWPOLYLINE":
        e.set_points([(*m.transform(Vec3(x, y, 0)).vec2, sw, ew, -b)
                      for x, y, sw, ew, b in e.get_points("xyseb")], format="xyseb")


# ── writing an instance ───────────────────────────────────────────────


def _rebuild_dicts(src_dict, dst_dict) -> None:
    for key, child in src_dict.items():
        if child.dxftype() != "DICTIONARY":
            raise NotImplementedError(f"extension-dict child {key!r} is a {child.dxftype()}")
        nd = dst_dict.add_new_dict(key, hard_owned=bool(child.dxf.get("hard_owned", 0)))
        _rebuild_dicts(child, nd)


def _xdict_rebuildable(d, path: str) -> None:
    for key, child in d.items():
        if child.dxftype() != "DICTIONARY":
            raise NotImplementedError(f"{path}/{key} is a {child.dxftype()}; copying it into a *U "
                                      "is not implemented")
        _xdict_rebuildable(child, f"{path}/{key}")


def _virtual_copy(src):
    """Unbound copy of *src* without its extension dict (rebuilt on binding).

    Raises NotImplementedError - before anything is written - for an entity
    ezdxf cannot copy (ACAD_TABLE ...) or whose extension dict holds more than
    dictionaries (a nested dynamic INSERT's representation, DIMASSOC, FIELD)."""
    from ezdxf.entities.copy import CopyNotSupported

    where = f"{src.dxftype()} {src.dxf.handle}"
    if src.has_extension_dict:
        _xdict_rebuildable(src.get_extension_dict().dictionary, f"{where} extension dict")
    try:
        c = src.copy()
    except CopyNotSupported as ex:
        raise NotImplementedError(f"{where} cannot be copied into a *U ({ex})") from None
    c.extension_dict = None
    return c


def _bind(src, c, block):
    """Bind virtual copy *c* of *src* into *block*: its own extension-dict chain,
    and the ``AcDbBlockRepETag`` XDATA handle pointing at ITSELF as CAD writes
    it (template *U1071: CIRCLE C02A1E carries 1005 C02A1E; ATTDEFs carry 0)."""
    block.add_entity(c)
    if src.has_extension_dict:
        xd = c.new_extension_dict()
        _rebuild_dicts(src.get_extension_dict().dictionary, xd.dictionary)
    if c.has_xdata("AcDbBlockRepETag"):
        c.set_xdata("AcDbBlockRepETag", [
            (t.code, c.dxf.handle if t.code == 1005 and t.value == src.dxf.handle else t.value)
            for t in c.get_xdata("AcDbBlockRepETag")])
    return c


def clone_entity(src, block):
    """Copy an entity into *block* the way CAD does for a ``*U`` representation.

    ezdxf's ``copy()`` re-handles the extension dictionary but SHARES its
    soft-owned (350) children - for an ATTDEF that leaves several dictionaries
    claiming one AcDbContextDataManager child ("non-unique entity handle" on
    reload).  So the copy's extension dict is dropped BEFORE binding and the
    dictionary chain rebuilt level by level.  The ``AcDbBlockRepETag`` handle is
    pointed at the copy itself, as in CAD's own ``*U`` blocks.
    """
    return _bind(src, _virtual_copy(src), block)


def _relink(sources: dict, clones: dict) -> None:
    """Point associative-hatch boundaries and reactors at the *U's own entities
    (template *U1071: HATCH C02A25 -> boundary C02A26, whose reactor is C02A25)."""
    h2c = {h: c.dxf.handle for h, c in clones.items()}
    for h, c in clones.items():
        if c.dxftype() == "HATCH":
            for p in c.paths:
                if p.source_boundary_objects:
                    p.source_boundary_objects = [h2c.get(x, x) for x in p.source_boundary_objects]
        src_reactors = sources[h].get_reactors()
        if src_reactors or c.get_reactors():
            c.set_reactors([h2c[x] for x in src_reactors if x in h2c])


def _move_vertex(ent, idx: int, d) -> None:
    from ezdxf.math import Vec3

    t = ent.dxftype()
    if t == "LINE":
        if idx == 0:
            ent.dxf.start = Vec3(ent.dxf.start) + d
        elif idx == 1:
            ent.dxf.end = Vec3(ent.dxf.end) + d
        else:
            raise DynamicBlockError(f"LINE has no vertex {idx}")
    elif t == "LWPOLYLINE":
        pts = list(ent.get_points())
        x, y, *rest = pts[idx]
        pts[idx] = (x + d.x, y + d.y, *rest)
        ent.set_points(pts)
    elif t in ("ATTDEF", "TEXT", "MTEXT", "INSERT", "CIRCLE") and idx == 0:
        ent.translate(d.x, d.y, d.z)   # a point entity: its one grip is its location
    elif t == "HATCH" and 0 <= idx < len(ent.seeds):
        _move_hatch_seed(ent, idx, d)
    else:
        raise NotImplementedError(f"stretch of {t} vertex {idx} is not implemented")


def _move_hatch_seed(h, idx: int, d) -> None:
    """A HATCH's stretch vertex *idx* is its seed point *idx*; the pattern origin
    (every pattern line's base point) moves with seed 0.

    Evidence - the owner's masonry block, AutoCAD ``*U3`` ('200 Full', W 190 -> 390): the
    hatch on boundary 10B, listed with vertex 0 in the W stretch, has its seed
    (0, 0) -> (200, 0) and its AR-SAND line base points (0, 0) -> (200, 0); its
    boundary is regenerated from the stretched polyline (:func:`_follow_boundary`)."""
    from ezdxf.math import Vec2

    seeds = [tuple(s) for s in h.seeds]
    x, y = seeds[idx]
    seeds[idx] = (x + d.x, y + d.y)
    h.seeds = seeds
    if idx == 0 and h.pattern is not None:
        for line in h.pattern.lines:
            line.base_point = Vec2(line.base_point) + Vec2(d.x, d.y)   # Vec2: ezdxf's writer reads .x


def _stretch_wipeout(w, verts: list, delta, action_id) -> list:
    """Stretch a WIPEOUT whose EVERY boundary vertex is in the stretch list, as
    AutoCAD writes it; returns ``[(handle, vertex index)]`` of the moved vertices.

    Evidence - the template's DETAIL LABEL ``*U15`` ("Line Length" 41.5 -> 49.0,
    ``tests/test_08_addons/dynblock_data/detail_label_truth.json``): the hexagon moves
    +7.5 mm whole, but CAD does not just move the insertion point - it rebuilds
    the image frame round the moved boundary: a SQUARE of side max(width,
    height) of the boundary's extents, anchored at their top-left corner
    (definition frame (3.5, -7.794) + 18 x 18 -> CAD *U (11.0, -10.206) + 18 x 18),
    with the boundary re-expressed in that frame's pixel coordinates (origin at
    the frame centre, y down).  A stretch that moves only SOME vertices has no
    CAD reference and raises.
    """
    from ezdxf.math import Vec2, Vec3

    where = f"stretch action {action_id}: WIPEOUT {w.dxf.handle}"
    d = w.dxf
    u, v = Vec3(d.u_pixel), Vec3(d.v_pixel)
    size = Vec2(d.image_size)
    if not (size.isclose(Vec2(1, 1)) and abs(u.y) < 1e-12 and abs(v.x) < 1e-12 and u.x > 0 and v.y > 0
            and abs(u.x - v.y) < 1e-9 and u.z == 0 and v.z == 0):
        raise NotImplementedError(f"{where}: only an axis-aligned square 1 x 1 pixel frame is implemented "
                                  f"(u {tuple(u)}, v {tuple(v)}, size {tuple(size)})")
    corners = w.boundary_path_wcs()
    closed = len(corners) > 2 and corners[-1].isclose(corners[0])
    distinct = len(corners) - 1 if closed else len(corners)
    if sorted(set(verts)) != list(range(distinct)):
        raise NotImplementedError(f"{where}: the stretch moves vertices {sorted(set(verts))} of "
                                  f"{distinct}; only moving every vertex has a CAD reference")
    moved = [(w.dxf.handle, i) for i in range(distinct)]
    pts = [c + delta for c in corners]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    x0, y1 = min(xs), max(ys)
    side = max(max(xs) - x0, y1 - min(ys))
    if side < 1e-12:
        raise DynamicBlockError(f"{where}: zero-size boundary")
    d.insert = Vec3(x0, y1 - side, Vec3(d.insert).z + delta.z)
    d.u_pixel, d.v_pixel = Vec3(side, 0, 0), Vec3(0, side, 0)
    w.set_boundary_path([((p.x - x0) / side - 0.5, (y1 - p.y) / side - 0.5) for p in pts])
    return moved


def _stretch_unlisted(a: dict, entity, moved: list, delta, ents: dict, refs: dict) -> None:
    """Entities in a stretch action's SELECTION SET (AcDbBlockAction group 330) with
    no stretch point in its vertex list (group 331 + 94), as AutoCAD writes them.

    Evidence - the template's Column Width 19.5 instances (AutoCAD ``*U1080``
    SCHEDULE ROW, ``*U1089`` SCHEDULE HEADER, ``tests/test_08_addons/dynblock_data/
    schedule_truth.json``): the MARK attribute definition, in the selection set but
    not the vertex list, is MOVED whole by the stretch (-7.5 mm with the left edge),
    and so is the instance's MARK attribute - BricsCAD's seed moves the attribute
    the same way; the associative heading-bar HATCH follows its stretched boundary
    polyline and its seed point moves with the stretch.  So:

    * an associative HATCH: every path point that sits on a stretched vertex of one
      of its boundary objects moves with it, and every seed point inside the
      stretch frame moves by the stretch;
    * any other entity is moved whole.

    A selection handle that is a graph node (grip, parameter) is skipped
    (*entity* - :func:`_evaluate`'s lookup - returns None for it and raises for a
    handle that is neither).  A non-associative HATCH raises: there is no CAD
    reference.

    Then EVERY associative hatch of the block whose boundary object was stretched
    follows it (:func:`_follow_boundary`), in the selection set or not: the owner's
    masonry block, AutoCAD ``*U3`` (W 190 -> 390) - the hatch on boundaries
    103 / 104, which is not in the W stretch's selection, is regenerated round the
    stretched boundaries.
    """
    from ezdxf.math import Vec2, Vec3

    listed = {h for h, _ in a["select"]}
    followed = set()
    for h in a["selection"]:
        e = None if h in listed else entity(h, a)
        if e is None:
            continue
        if e.dxftype() != "HATCH":
            e.translate(delta.x, delta.y, delta.z)
            continue
        sources = _hatch_sources(e)
        if not e.dxf.get("associative", 0) or not sources:
            raise NotImplementedError(f"stretch action {a['id']}: HATCH {h} is in the selection set "
                                      "but not associative - no CAD reference for moving it")
        _follow_boundary(e, refs[h], moved, delta, a["id"])
        followed.add(h)
        if len(a["frame"]) != 2:
            raise NotImplementedError(f"stretch action {a['id']}: a {len(a['frame'])}-point (polygon) "
                                      "frame is not implemented")
        (x0, y0), (x1, y1) = [Vec3(c).vec2 for c in a["frame"]]
        lo, hi = Vec2(min(x0, x1), min(y0, y1)), Vec2(max(x0, x1), max(y0, y1))
        e.seeds = [(x + delta.x, y + delta.y) if lo.x <= x <= hi.x and lo.y <= y <= hi.y else (x, y)
                   for x, y in e.seeds]
    stretched = {s for s, _ in moved}
    for h, e in ents.items():
        if h in followed or h not in refs:
            continue
        if _hatch_sources(e) & stretched:
            _follow_boundary(e, refs[h], moved, delta, a["id"])


def _hatch_sources(h) -> set:
    return {s for p in h.paths for s in (p.source_boundary_objects or [])}


def _boundary_refs(h, ents: dict) -> list:
    """Which boundary-object vertices each point of associative HATCH *h* sits on,
    read ONCE on the unstretched definition: per path, per polyline vertex a set
    of ``(source handle, vertex index)``; per edge a ``(start set, end set)`` (line
    / arc edges) or None (other edges).

    By identity, not by position, because a stretch can fold one boundary vertex
    onto another: the owner's masonry block '200 Section' with Height 95 (CAD ``*U10``) -
    D = 0 moves 11B's vertex 0 onto its vertex 9 at (-194.2, 0), and the Height
    stretch then moves vertex 0 again but not vertex 9; matching the hatch's points
    by position moved both (a 190 x 95 mm error in the hatch region)."""
    from ezdxf.entities.boundary_paths import ArcEdge, LineEdge, PolylinePath
    from ezdxf.math import Vec2

    verts = []
    for s in _hatch_sources(h):
        e = ents.get(s)
        if e is None:
            continue
        if e.dxftype() == "LWPOLYLINE":
            pts = [Vec2(x, y) for x, y in e.get_points("xy")]
        elif e.dxftype() == "LINE":
            pts = [Vec2(e.dxf.start), Vec2(e.dxf.end)]
        else:
            continue
        verts += [((s, i), p) for i, p in enumerate(pts)]

    # 1e-9 mm: on the masonry block line-edge AND arc-edge ends (the latter computed
    # from centre / radius / angle) sit within 6e-13 mm of their polyline vertices
    def ref(q, tol: float = 1e-9) -> frozenset:
        return frozenset(k for k, p in verts if abs(p.x - q[0]) <= tol and abs(p.y - q[1]) <= tol)

    out: list[list] = []
    for p in h.paths:
        if isinstance(p, PolylinePath):
            out.append([ref((x, y)) for x, y, _ in p.vertices])
            continue
        row: list[Optional[tuple]] = []
        for g in p.edges:
            if isinstance(g, LineEdge):
                row.append((ref(g.start), ref(g.end)))
            elif isinstance(g, ArcEdge):
                c = Vec2(g.center)
                row.append(tuple(ref(c + Vec2.from_deg_angle(ang, g.radius))
                                 for ang in (g.start_angle, g.end_angle)))
            else:
                row.append(None)
        out.append(row)
    return out


def _follow_boundary(h, refs: list, moved: list, delta, action_id) -> None:
    """Move the boundary-path points of associative HATCH *h* that sit on a
    stretched vertex of its boundary object by *delta* - *refs* from
    :func:`_boundary_refs`, *moved* the ``(handle, vertex index)`` pairs this
    stretch moved.  An arc edge moves whole when both its ends sit on stretched
    vertices and stays when neither does; an arc with ONE end moved would have to
    be re-fitted (CAD keeps the polyline's bulge) - not implemented, raises."""
    from ezdxf.entities.boundary_paths import ArcEdge, LineEdge, PolylinePath
    from ezdxf.math import Vec2

    ids = set(moved)
    d = Vec2(delta.x, delta.y)

    def follow(q, r) -> Vec2:
        return Vec2(q) + d if r & ids else Vec2(q)

    for p, pr in zip(h.paths, refs):
        if isinstance(p, PolylinePath):
            p.vertices = [(*follow((x, y), r), b) for (x, y, b), r in zip(p.vertices, pr)]
            continue
        for g, r in zip(p.edges, pr):
            if isinstance(g, LineEdge):
                g.start, g.end = follow(g.start, r[0]), follow(g.end, r[1])
            elif isinstance(g, ArcEdge):
                hit = [bool(x & ids) for x in r]
                if all(hit):
                    g.center = Vec2(g.center) + d
                elif any(hit):
                    raise NotImplementedError(f"stretch action {action_id}: HATCH {h.dxf.handle} arc edge "
                                              "with one end stretched is not implemented")
            else:
                raise NotImplementedError(f"stretch of a HATCH {type(g).__name__} is not implemented")


def _repdata_constants(library, name: str) -> tuple[int, dict]:
    lib = _library(library)
    try:
        d = lib.rootdict[REPDATA_KEY][name]
    except (KeyError, TypeError):
        raise DynamicBlockError(
            f"the block library carries no representation constants for {name!r} "
            f"(rootdict/{REPDATA_KEY}/{name}); rebuild it with "
            "a block library builder (see the module docs: one CAD-made seed instance per block)") from None
    flag, nodes = 1, {}
    for key, xr in d.items():
        if key == _REPDATA_FLAG:
            flag = next((t.value for t in xr.tags if t.code == 70), 1)
        else:
            nodes[key] = list(xr.tags)
    return flag, nodes


def _new_repdata(doc, flag: int, parent_handle: str, owner: str):
    """ACDB_BLOCKREPRESENTATION_DATA - not modelled by ezdxf and cannot be
    ``copy()``'d, so it is written as DXF text and loaded, which sets up owner /
    reactors / subclass exactly as a file load would."""
    from ezdxf.entities.dxfentity import DXFTagStorage
    from ezdxf.lldxf.extendedtags import ExtendedTags

    text = "\n".join([
        "0", "ACDB_BLOCKREPRESENTATION_DATA", "5", doc.entitydb.next_handle(),
        "102", "{ACAD_REACTORS", "330", owner, "102", "}",
        "330", owner,
        "100", "AcDbBlockRepresentationData",
        "70", str(flag),
        "340", parent_handle,
    ]) + "\n"
    new = DXFTagStorage.load(ExtendedTags.from_text(text), doc)
    doc.entitydb.add(new)
    doc.objects.add_object(new)
    return new


def _check_value_set(p: dict, value: float, unit: str = "") -> None:
    """Min / max / increment of a linear (drawing units) or rotation (radians) parameter.

    The increment grid is anchored at the DEFINITION value, not the minimum:
    every CAD-made template instance of the 8 blocks whose definition value is
    off the min-anchored grid sits on the definition-anchored one (41 of 41:
    Break Line 183 + n*50, DETAIL LABEL 41.5 + n*2.5 -> 49.0)."""
    flags, what = p.get("flags", 0) or 0, p["label"]
    if flags & ~(_VS_MIN | _VS_MAX | _VS_INC):
        raise NotImplementedError(f"parameter {what!r} has a value LIST (group 96 = {flags}); "
                                  "writing a non-default value is not implemented")
    if flags & _VS_MIN and value < p["min"] - 1e-9:
        raise ValueError(f"{value}{unit} is below the {what!r} minimum {p['min']}{unit}")
    if flags & _VS_MAX and value > p["max"] + 1e-9:
        raise ValueError(f"{value}{unit} is above the {what!r} maximum {p['max']}{unit}")
    if flags & _VS_INC and p["inc"]:
        anchor = p["default"]
        snapped = anchor + round((value - anchor) / p["inc"]) * p["inc"]
        if abs(snapped - value) > 1e-6:
            raise ValueError(f"{value}{unit} is off the {p['inc']} increment grid of {what!r} "
                             f"(nearest {snapped})")


def _check_linear(p: dict, v) -> float:
    """A linear parameter value: >= 0 and inside the parameter's value set.

    Zero is allowed: CAD's own lookup rows set a distance of 0 (masonry block
    'Section' rows set D = 0; instances *U6 / *U7 / *U8 / *U10 of the owner's drawing
    carry it), which leaves the parameter's two points coincident."""
    v = float(v)
    if v < 0:
        raise ValueError(f"{p['label']!r} must be >= 0, got {v}")
    _check_value_set(p, v)
    return v


def _resolve_targets(graph: dict, rotation_deg, flip, visibility, linear, lookup=None) -> dict:
    """User values -> ``{parameter id: value}`` (validated; degrees, bool, state, distance).

    A *lookup* entry sets its parameter AND every value its table row carries
    (visibility state, distances); an explicit value for one of those must equal
    the row's, else ValueError."""
    name, params = graph["name"], graph["params"]
    out: dict = {}
    for kind, value, arg in (("rotation", rotation_deg, "rotation_deg"), ("flip", flip, "flip"),
                             ("visibility", visibility, "visibility"), ("linear", linear, "linear"),
                             ("lookup", lookup, "lookup")):
        if value is None:
            continue
        ps = [p for p in params.values() if p["kind"] == kind]
        if not ps:
            raise ValueError(f"block {name!r} has no {kind} parameter ({arg}= given)")
        if isinstance(value, dict):
            pairs = []
            for key, v in value.items():
                match = [p for p in ps if key in (p["id"], p["label"], p["name"])]
                if len(match) != 1:
                    raise ValueError(f"block {name!r} has {'no' if not match else 'several'} {kind} "
                                     f"parameter {key!r} (has {[p['label'] for p in ps]})")
                pairs.append((match[0], v))
        else:
            if len(ps) > 1:
                raise ValueError(f"block {name!r} has {len(ps)} {kind} parameters "
                                 f"{[p['label'] for p in ps]}; pass {arg}= as {{label: value}}")
            pairs = [(ps[0], value)]
        for p, v in pairs:
            if kind == "linear":
                v = _check_linear(p, v)
            elif kind == "lookup":
                a, row = _lookup_row(graph, p["id"], v)
                for i, (col, cell) in enumerate(zip(a["columns"], row)):
                    if i == a["lookup_col"]:
                        continue
                    q = params[col["param_id"]]
                    if q["kind"] == "linear":
                        cell = _check_linear(q, cell)
                    if q["id"] in out:
                        given = out[q["id"]]
                        same = abs(given - cell) <= 1e-6 if q["kind"] == "linear" else given == cell
                        if not same:
                            raise ValueError(f"block {name!r}: lookup {p['label']!r} = {v!r} sets "
                                             f"{q['label']!r} to {cell!r}, which conflicts with the "
                                             f"{given!r} given")
                    out[q["id"]] = cell
            elif kind == "rotation":
                v = float(v) % 360.0
                _check_value_set({**p, "default": math.radians(p["default"])}, math.radians(v), " rad")
            elif kind == "flip":
                if isinstance(v, str):
                    if v not in p["states"]:
                        raise ValueError(f"{p['label']!r} flip state {v!r} is not one of {list(p['states'])}")
                    v = v == p["states"][1]
                elif not isinstance(v, (bool, int)):
                    raise ValueError(f"flip must be a bool or a state name, got {v!r}")
                v = bool(v)
            else:
                if v not in p["states"]:
                    raise ValueError(f"block {name!r} has no visibility state {v!r} "
                                     f"(states {p['state_names']})")
            out[p["id"]] = v
    return out


def _evaluate(graph: dict, targets: dict, ents: dict) -> tuple[dict, dict]:
    """Apply the block's actions to the (virtual) *U entities in graph order.

    Returns ``(points, values)``: every parameter's final points (definition ->
    instance coordinates) and its final value (distance / degrees / bool / state).
    """
    from ezdxf.math import Vec3

    params, actions = graph["params"], graph["actions"]
    pts = {pid: [Vec3(v) for v in p["points"]] for pid, p in params.items()}
    refs = {h: _boundary_refs(e, ents) for h, e in ents.items()
            if e.dxftype() == "HATCH" and e.dxf.get("associative", 0) and _hatch_sources(e)}
    change: dict = {}
    values: dict = {}

    def entity(h, a):
        e = ents.get(h)
        if e is None and h not in graph.get("node_handles", ()):
            raise DynamicBlockError(f"action {a['id']} selects {h}, which is not in the block")
        return e

    for nid in graph["order"]:
        if nid in params:
            p = params[nid]
            old = list(pts[nid])
            new = list(old)
            v = targets.get(nid, p["default"])
            if p["kind"] == "linear":
                if p.get("base_location", 0) != 0 and nid in targets:
                    raise NotImplementedError(f"{p['label']!r}: base location {p['base_location']} (group 175; "
                                              "only 0 = start point is implemented)")
                axis = old[1] - old[0]
                if axis.magnitude < 1e-12:
                    raise DynamicBlockError(f"{p['label']!r} has zero length")
                new[1] = old[0] + axis.normalize(v)
            elif p["kind"] == "rotation":
                base, end, ref = old
                r = (end - base).magnitude
                new[1] = base + Vec3.from_angle((ref - base).angle + math.radians(v), r)
            values[nid] = v
            change[nid] = (old, new)
            pts[nid] = new
            continue
        a = actions[nid]
        if a["kind"] == "lookup":
            continue        # applied to the targets already (_resolve_targets)
        old, new = change[a["param_id"]]
        if a["kind"] == "stretch":
            delta = new[a["point"]] - old[a["point"]]
            if a["multiplier"] != 1.0 or a["angle_offset"] != 0.0:
                if delta.magnitude > 1e-12:
                    raise NotImplementedError(f"stretch {a['id']}: distance multiplier / angle offset "
                                              "is not implemented")
            if delta.magnitude < 1e-12:
                continue
            moved = []      # (entity handle, vertex index) of every stretched vertex
            for h, verts in a["select"]:
                e = entity(h, a)
                if e is not None and e.dxftype() == "WIPEOUT":
                    moved += _stretch_wipeout(e, verts, delta, a["id"])
                elif e is not None:
                    for i in verts:
                        _move_vertex(e, i, delta)
                        moved.append((h, i))
            _stretch_unlisted(a, entity, moved, delta, ents, refs)
            for pid, idx in a["param_points"]:
                if pid in pts:
                    for i in idx:
                        pts[pid][i] = pts[pid][i] + delta
        elif a["kind"] == "rotate":
            ang = (new[1] - new[0]).angle - (old[1] - old[0]).angle
            if abs(math.sin(ang)) < 1e-15 and math.cos(ang) > 0:
                continue
            center = new[0] + a["offset"] if a["dependent"] else a["base_point"]
            m = _xy_rotation(center, ang)
            for h in a["selection"]:
                e = entity(h, a)
                if e is not None:
                    _transform_entity(e, m, mirror=False)
            for pid in a["param_ids"]:
                if pid in pts:
                    pts[pid] = [m.transform(q) for q in pts[pid]]
        else:   # flip
            if not values.get(a["param_id"]):
                continue
            p1, p2 = pts[a["param_id"]][:2]
            m = _xy_mirror(p1, p2)
            for h in a["selection"]:
                e = entity(h, a)
                if e is not None:
                    _transform_entity(e, m, mirror=True, line_angle=(p2 - p1).angle)
            for pid in a["param_ids"]:
                if pid in pts:
                    pts[pid] = [m.transform(q) for q in pts[pid]]
    # visibility: an entity governed by a visibility parameter is shown only if
    # the chosen state lists it (template 'Marker Only' hides 11 of 18)
    hidden, governed = set(), set()
    for pid, p in params.items():
        if p["kind"] == "visibility":
            state = values.get(pid, p["default"])
            values[pid] = state
            governed |= set(p["governed"])
            hidden |= set(p["governed"]) - set(p["states"][state])
    for h in governed:
        if h in ents:
            ents[h].dxf.invisible = 1 if h in hidden else 0
    _reverse_lookup(graph, values)
    return pts, values


#: Layout of each node's representation XRECORD after the 4-tag header
#: (1071, 1071, 70, 70) - measured on the template's SECTION MARKER instances and
#: the library seeds.  Anything else raises rather than being written blind.
_STATE_LAYOUT = {
    "BLOCKLINEARPARAMETER": [10, 10, 10],            # base, end, normal
    "BLOCKROTATIONPARAMETER": [10, 10, 10, 10],      # base, end, normal, angle reference point
    "BLOCKFLIPPARAMETER": [10, 10, 10, 70],          # base, end, normal, flipped
    "BLOCKVISIBILITYPARAMETER": [10, 1],             # location, state name
    "BLOCKLOOKUPPARAMETER": [10, 1],                 # location, lookup entry (masonry block)
    "BLOCKROTATEACTION": [10],                       # rotation centre
    "BLOCKSTRETCHACTION": [40],                      # 0.0 in every CAD instance - kept
    "BLOCKFLIPGRIP": [70, 70],                       # ?, flipped
}


def _state_tags(key: str, tags: list, graph: dict, pts: dict, values: dict) -> list:
    from ezdxf.lldxf.types import DXFTag, DXFVertex

    nid = int(key)
    node = graph["params"].get(nid) or graph["actions"].get(nid)
    typ = node["type"] if node else ("BLOCKFLIPGRIP" if nid in graph["grips"] else None)
    layout = _STATE_LAYOUT.get(typ) if typ is not None else None
    head, body = list(tags[:4]), list(tags[4:])
    if layout is None or [t.code for t in body] != layout or [t.code for t in head] != [1071, 1071, 70, 70]:
        raise DynamicBlockError(f"{graph['name']!r}: no rule to write the state of node {key} ({typ}) "
                                f"with tags {[t.code for t in tags]}")
    if typ in ("BLOCKLINEARPARAMETER", "BLOCKROTATIONPARAMETER", "BLOCKFLIPPARAMETER"):
        cur = pts[nid]
        body[0], body[1] = DXFVertex(10, tuple(cur[0])), DXFVertex(10, tuple(cur[1]))
        if typ == "BLOCKROTATIONPARAMETER":
            body[3] = DXFVertex(10, tuple(cur[2]))
        if typ == "BLOCKFLIPPARAMETER":
            body[3] = DXFTag(70, int(values[nid]))
    elif typ in ("BLOCKVISIBILITYPARAMETER", "BLOCKLOOKUPPARAMETER"):
        body = [DXFVertex(10, tuple(pts[nid][0])), DXFTag(1, values[nid])]
    elif typ == "BLOCKROTATEACTION":
        center = pts[node["param_id"]][0] + node["offset"] if node["dependent"] else node["base_point"]
        body[0] = DXFVertex(10, tuple(center))
    elif typ == "BLOCKFLIPGRIP":
        body[1] = DXFTag(70, int(values[graph["grips"][nid]]))
    return head + body


def _state_dict(graph: dict, values: dict) -> dict:
    """``{label: value}`` - parameter labels made unique with the node name if needed."""
    labels = [p["label"] for p in graph["params"].values()]
    out = {}
    for pid, p in graph["params"].items():
        key = p["label"] if labels.count(p["label"]) == 1 else f"{p['label']} ({p['name']}#{pid})"
        v = values[pid]
        out[key] = float(v) if p["kind"] in ("linear", "rotation") else v
    return out


def add_dynamic(doc, layout, name: str, insert, *, rotation_deg=None, flip=None, visibility=None,
                linear=None, lookup=None, attribs: Optional[dict] = None, library=None,
                dxfattribs: Optional[dict] = None):
    """Insert dynamic block *name* in a chosen dynamic state, in pure DXF.

    Args:
        doc: the drawing (R2018+); *name* must be defined in it
            (:func:`define_dynamic_blocks`).
        layout: modelspace / paperspace / block layout to place the INSERT in.
        insert: insertion point (block origin), drawing units.
        rotation_deg: rotation parameter value, degrees counter-clockwise from
            the parameter's reference direction (SECTION MARKER "Arm Angle").
            A number when the block has one rotation parameter, else
            ``{label: degrees}``.
        flip: True / False, or the flip state name ("Flipped"); ``{label: ...}``
            for several flip parameters.
        visibility: visibility state name ("Marker Only").
        linear: ``{label: distance}`` - each linear parameter's final
            base-to-end distance (SECTION MARKER "Label Arm" / "Arm Gap" /
            "Tail Arm").  Parameters not given keep their DEFINITION distance
            even when an upstream stretch moved their base (chained arms: the
            downstream arm translates, it does not shrink).  Must respect the
            parameter's minimum / maximum / increment grid.
        lookup: a lookup-table entry (masonry block "Block": ``"150 Full"``),
            or ``{label: entry}`` for several lookup parameters.  The entry's
            row sets the visibility state and distances it carries, and the
            lookup parameter's own state is written as the entry.  An explicit
            *visibility* / *linear* value for a parameter the row sets must
            equal the row's value.  Not given: the lookup state is the entry
            whose row matches the evaluated values, else the table's no-match
            label ('Custom').
        attribs: ``{tag: text}`` for ``add_auto_attribs``.
        library: where the representation constants come from (default
            :data:`LIBRARY_PATH`).
        dxfattribs: INSERT attributes (``xscale`` / ``yscale`` / ``zscale``,
            ``rotation``, ``layer`` ...), applied before the ATTRIBs are
            placed so they follow the insert.  The dynamic values stay in
            block units (a label scaled x20 into model space keeps its paper-mm
            "Line Length").

    How: the definition's entities are copied, the block's actions are applied
    in the graph's evaluation order (:func:`read_graph` - stretches move the
    selected vertices, a rotate action turns its selection about the rotation
    parameter's base, a flip action mirrors its selection about the flip line,
    visibility sets the DXF invisible flag), the copies are bound into a new
    ``*U`` block tagged back to the parent, and the INSERT gets the
    ``AcDbBlockRepresentation`` XRECORDs: the library seed's constants with only
    the state values rewritten.  Everything is validated and evaluated before
    the first entity is added to *doc*, so a failure writes nothing.

    Returns:
        ``(insert_entity, "*U.." block name, state)`` - ``state`` is ``{label:
        value}`` for every parameter as written (see :func:`read_dynamic_state`).

    Raises:
        NotImplementedError: the block has node types :func:`read_graph` lists
            as unsupported, or an action would have to transform an entity type
            it cannot (e.g. flipping TEXT).
        ValueError: unknown label / state / lookup entry, value outside the
            parameter's value set, a lookup row conflicting with an explicit
            value, drawing below R2018.
        KeyError: block not defined.
        DynamicBlockError: library constants missing or of an unexpected layout.
    """
    from ezdxf.lldxf.types import DXFVertex  # noqa: F401 - fail early if ezdxf is missing

    _require_r2018(doc, "add_dynamic")
    if name not in doc.blocks:
        raise KeyError(f"block {name!r} is not defined in the drawing; call define_dynamic_blocks first")
    parent = doc.blocks.get(name)
    graph = read_graph(doc, name)
    if graph["unsupported"]:
        raise NotImplementedError(f"block {name!r} has dynamic node types {graph['unsupported']} that "
                                  "add_dynamic does not evaluate (supported: linear/stretch, rotation/"
                                  "rotate, flip, visibility, lookup)")
    graph["node_handles"] = {n.dxf.handle for n in graph_nodes(doc, parent.block_record)}
    targets = _resolve_targets(graph, rotation_deg, flip, visibility, linear, lookup)
    flag, constants = _repdata_constants(library, name)
    sources = {e.dxf.handle: e for e in parent}
    ents = {h: _virtual_copy(e) for h, e in sources.items()}
    pts, values = _evaluate(graph, targets, ents)
    records = {key: _state_tags(key, tags, graph, pts, values) for key, tags in constants.items()}

    u = doc.blocks.new_anonymous_block(type_char="U", base_point=parent.block.dxf.base_point)
    clones = {h: _bind(sources[h], ents[h], u) for h in sources}
    _relink(sources, clones)
    order = {clones[h].dxf.handle: (clones[s].dxf.handle if s in clones else s)
             for h, s in parent.get_redraw_order() if h in clones}
    if order:   # keep the definition's draw order (e.g. text over the header fill)
        u.set_redraw_order(order)
    # the APPID must be registered: a fresh ezdxf.new() doc lacks it and AutoCAD then
    # rejects the whole BLOCK_RECORD table ("Premature end of object")
    _ensure_appid(doc, "AcDbBlockRepBTag")
    u.block_record.set_xdata("AcDbBlockRepBTag", [(1070, 1), (1005, parent.block_record.dxf.handle)])

    ins = layout.add_blockref(u.name, insert, dxfattribs=dict(dxfattribs or {}))
    if attribs:
        ins.add_auto_attribs(attribs)
    xd = ins.new_extension_dict()
    rep = xd.dictionary.add_new_dict("AcDbBlockRepresentation", hard_owned=True)
    rep.add("AcDbRepData", _new_repdata(doc, flag, parent.block_record.dxf.handle, rep.dxf.handle))
    cache = rep.add_new_dict("AppDataCache", hard_owned=True)
    ebd = cache.add_new_dict("ACAD_ENHANCEDBLOCKDATA", hard_owned=True)
    for key, tags in records.items():
        ebd.add_xrecord(key).reset(tags)
    return ins, u.name, _state_dict(graph, values)


def read_dynamic_state(insert) -> dict:
    """``{label: value}`` of a dynamic-block INSERT, read from its representation XRECORDs.

    Linear: base-to-end distance; rotation: degrees from the parameter's
    reference direction, [0, 360); flip: bool; visibility: state name.  A
    parameter with no XRECORD (or an INSERT of the definition itself) reads as
    its definition default.
    """
    from ezdxf.math import Vec3

    doc = insert.doc
    blk = doc.blocks.get(insert.dxf.name)
    name = blk.name
    if blk.block_record.has_xdata("AcDbBlockRepBTag"):
        h = next(t.value for t in blk.block_record.get_xdata("AcDbBlockRepBTag") if t.code == 1005)
        name = doc.entitydb[h].dxf.name
    graph = read_graph(doc, name)
    if graph["unsupported"]:
        raise NotImplementedError(f"block {name!r} has unsupported node types {graph['unsupported']}")
    ebd = None
    if insert.has_extension_dict:
        try:
            ebd = insert.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
        except (KeyError, TypeError):
            ebd = None
    values = {}
    for pid, p in graph["params"].items():
        xr = ebd.get(str(pid)) if ebd is not None else None
        if xr is None:
            values[pid] = p["default"]
            continue
        body = list(xr.tags)[4:]
        pts = [Vec3(t.value) for t in body if t.code == 10]
        if p["kind"] == "linear":
            values[pid] = (pts[1] - pts[0]).magnitude
        elif p["kind"] == "rotation":
            ref = pts[3] if len(pts) > 3 else p["angle_point"]
            values[pid] = math.degrees((pts[1] - pts[0]).angle - (ref - pts[0]).angle) % 360.0
        elif p["kind"] == "flip":
            values[pid] = bool([t.value for t in body if t.code == 70][-1])
        else:
            values[pid] = next(t.value for t in body if t.code == 1)
    _reverse_lookup(graph, values)      # a lookup parameter with no XRECORD
    return _state_dict(graph, values)


def add_stretched(doc, layout, name: str, insert, distance: float, attribs: Optional[dict] = None,
                  *, parameter: Optional[str] = None, library=None):
    """Insert dynamic block *name* with one linear parameter set to *distance*.

    A thin wrapper over :func:`add_dynamic` (every other parameter at its
    definition default).

    Args:
        doc: the drawing; *name* must be defined in it (:func:`define_dynamic_blocks`).
        layout: modelspace / paperspace / block layout to place the INSERT in.
        insert: insertion point (block origin), drawing units.
        distance: parameter value, e.g. NOTES "Line Height" in mm.  Must be
            >= the parameter minimum and on its increment grid (NOTES: min 5.0,
            increment 3.5 from the 5.0 minimum).
        attribs: ``{tag: text}`` for ``add_auto_attribs``.
        parameter: linear parameter label; required for blocks with several.
        library: where the representation constants come from (default
            :data:`LIBRARY_PATH`).

    Returns:
        ``(insert_entity, "*U.." block name, param dict)``.

    Raises:
        ValueError: off-grid / below-minimum distance, ambiguous parameter.
        NotImplementedError: the block has node types add_dynamic does not
            evaluate (see :func:`read_graph`).
        DynamicBlockError: library constants missing.
    """
    _require_r2018(doc, "add_stretched")
    if name not in doc.blocks:
        raise KeyError(f"block {name!r} is not defined in the drawing; call define_dynamic_blocks first")
    param, _ = read_linear_stretch(doc, name, parameter)
    ins, uname, _ = add_dynamic(doc, layout, name, insert, linear={param["id"]: distance},
                                attribs=attribs, library=library)
    return ins, uname, param


# ── survey: which blocks of a drawing can be placed ──────────────────

#: Node types that only place interactive grips; not listed as parameters / actions.
_GRIP_NODE_TYPES = frozenset({"ACAD_EVALUATION_GRAPH", "BLOCKGRIPLOCATIONCOMPONENT"})


def _node_kind(dxftype: str) -> str:
    if dxftype.endswith("PARAMETER"):
        return "parameter"
    if dxftype.endswith("ACTION"):
        return "action"
    if dxftype.endswith("GRIP") or dxftype in _GRIP_NODE_TYPES:
        return "grip"
    return "other"


def _entity_problems(doc, block, graph: dict) -> list[str]:
    """Reasons :func:`add_dynamic` would refuse *block*, found without writing
    anything: entities that cannot be copied into a ``*U`` block, and entity types
    a flip or rotate action could not transform.  (A stretch problem can depend on
    the values placed; it is found by :func:`add_dynamic` itself.)"""
    problems = []
    ents = {e.dxf.handle: e for e in block}
    for e in ents.values():
        try:
            _virtual_copy(e)
        except NotImplementedError as ex:
            problems.append(str(ex))
    for a in graph["actions"].values():
        if a["kind"] not in ("flip", "rotate"):
            continue
        allowed = _MIRRORABLE if a["kind"] == "flip" else _ROTATABLE
        bad = sorted({ents[h].dxftype() for h in a["selection"]
                      if h in ents and ents[h].dxftype() not in allowed})
        if bad:
            problems.append(f"{a['kind']} action {a['id']} ({a['name']!r}) selects {bad}, "
                            f"which a {a['kind']} cannot transform yet")
    return problems


def survey(doc, names: Optional[Iterable[str]] = None, library=None) -> list[dict]:
    """Which dynamic blocks of *doc* this module can place, and why not.

    Read-only.  One record per named dynamic block (anonymous ``*`` blocks and
    static blocks are skipped)::

        {"name", "status", "reasons": [str], "parameters": {type: count},
         "actions": {type: count}, "unsupported": [type], "seed": bool | None}

    ``status`` is ``"supported"`` (the graph evaluates and every entity can be
    copied and transformed), ``"unsupported"`` (node types outside
    :data:`SUPPORTED_NODE_TYPES`), ``"not implemented"`` (supported node types,
    but a feature of this block is not: a dependency cycle, an XY stretch, an
    entity that cannot be copied or transformed ...) or ``"unreadable"`` (a
    graph layout the parser does not know).  ``seed`` tells whether *library*
    holds the representation constants needed to place the block (``None``
    without a library).

    Args:
        doc: drawing to survey, e.g. a CAD template saved as DXF
        names: block names to survey; default every named dynamic block
        library: optional block library (path or Drawing) to check for seeds
    """
    from collections import Counter

    lib = _library(library) if library is not None else None
    seeds = set()
    if lib is not None and REPDATA_KEY in lib.rootdict:
        seeds = set(lib.rootdict[REPDATA_KEY].keys())
    wanted = set(names) if names is not None else None
    out = []
    for block in doc.blocks:
        name = block.name
        if wanted is not None:
            if name not in wanted:
                continue
        elif name.startswith("*") or block.is_any_layout:
            continue
        nodes = graph_nodes(doc, block.block_record)
        if not nodes:
            continue
        types = [n.dxftype() for n in nodes]
        rec: dict = {
            "name": name,
            "status": "supported",
            "reasons": [],
            "parameters": dict(Counter(t for t in types if _node_kind(t) == "parameter")),
            "actions": dict(Counter(t for t in types if _node_kind(t) == "action")),
            "unsupported": sorted(set(types) - SUPPORTED_NODE_TYPES),
            "seed": (name in seeds) if lib is not None else None,
        }
        if rec["unsupported"]:
            rec["status"] = "unsupported"
            rec["reasons"] = [f"node types {rec['unsupported']}"]
        else:
            try:
                graph = read_graph(doc, name)
            except NotImplementedError as ex:
                rec["status"], rec["reasons"] = "not implemented", [str(ex)]
            except (DynamicBlockError, KeyError, StopIteration, TypeError, ValueError) as ex:
                rec["status"], rec["reasons"] = "unreadable", [f"{type(ex).__name__}: {ex}"]
            else:
                problems = _entity_problems(doc, block, graph)
                if problems:
                    rec["status"], rec["reasons"] = "not implemented", problems
        out.append(rec)
    return out


def survey_summary(records: list[dict]) -> dict:
    """Totals of :func:`survey` records: blocks per status, and for every node
    type not supported the number of blocks it blocks - the order in which
    adding node types would make the most blocks placeable."""
    from collections import Counter

    status = Counter(r["status"] for r in records)
    blocking = Counter(t for r in records for t in r["unsupported"])
    only = Counter(r["unsupported"][0] for r in records if len(r["unsupported"]) == 1)
    return {
        "blocks": len(records),
        "status": dict(status),
        "unsupported_types": blocking.most_common(),
        "sole_blocker": only.most_common(),
        "seedless_supported": sorted(r["name"] for r in records
                                     if r["status"] == "supported" and r["seed"] is False),
    }


def _main(argv=None) -> int:
    """``python -m ezdxf.addons.dynblock DRAWING.dxf [--library LIB.dxf] [--csv OUT.csv]``"""
    import argparse
    import csv
    import sys

    import ezdxf

    ap = argparse.ArgumentParser(prog="python -m ezdxf.addons.dynblock",
                                 description="Survey which dynamic blocks of a DXF drawing can be placed.")
    ap.add_argument("drawing")
    ap.add_argument("--library", help="block library DXF to check for representation seeds")
    ap.add_argument("--csv", help="write one row per block to this CSV file")
    args = ap.parse_args(argv)
    doc = ezdxf.readfile(args.drawing)
    records = survey(doc, library=args.library)
    width = max((len(r["name"]) for r in records), default=4)
    for r in sorted(records, key=lambda r: (r["status"], r["name"])):
        seed = "" if r["seed"] is None else (" seed" if r["seed"] else " NO SEED")
        print(f"{r['name']:<{width}}  {r['status']:<15}{seed}  {'; '.join(r['reasons'])[:160]}")
    summary = survey_summary(records)
    print(f"\n{summary['blocks']} dynamic blocks: {summary['status']}")
    print("unsupported node types (blocks affected):", summary["unsupported_types"])
    print("the only missing type of a block (blocks):", summary["sole_blocker"])
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["name", "status", "seed", "parameters", "actions", "unsupported", "reasons"])
            for r in records:
                w.writerow([r["name"], r["status"], r["seed"],
                            " ".join(f"{k}={v}" for k, v in sorted(r["parameters"].items())),
                            " ".join(f"{k}={v}" for k, v in sorted(r["actions"].items())),
                            " ".join(r["unsupported"]), " | ".join(r["reasons"])])
        print(f"wrote {args.csv}", file=sys.stderr)
    return 0


# ── multi-line attributes ─────────────────────────────────────────────

_MTEXT_PROPS = ("char_height", "width", "style", "attachment_point", "line_spacing_factor",
                "line_spacing_style", "layer", "color", "text_direction")


def _attdef_for(insert, tag: str):
    doc = insert.doc
    blk = doc.blocks.get(insert.dxf.name)
    att = next((a for a in blk.query("ATTDEF") if a.dxf.tag == tag), None)
    if att is None and blk.block_record.has_xdata("AcDbBlockRepBTag"):
        parent = doc.entitydb.get(next(t.value for t in blk.block_record.get_xdata("AcDbBlockRepBTag")
                                       if t.code == 1005))
        if parent is not None:
            att = next((a for a in doc.blocks.get(parent.dxf.name).query("ATTDEF") if a.dxf.tag == tag), None)
    if att is None:
        raise KeyError(f"block {insert.dxf.name!r} has no ATTDEF {tag!r}")
    return att


def mtext_props_for(insert, tag: str) -> dict:
    """The MTEXT properties (height, width, style, ...) of ATTDEF *tag*'s embedded MTEXT."""
    attdef = _attdef_for(insert, tag)
    if not attdef.has_embedded_mtext_entity:
        raise ValueError(f"ATTDEF {tag!r} is not a multi-line attribute")
    vm = attdef.virtual_mtext_entity()
    return {k: vm.dxf.get(k) for k in _MTEXT_PROPS if vm.dxf.hasattr(k)}


def set_multiline_attrib(insert, tag: str, text: str):
    """Put *text* into the multi-line (embedded MTEXT) ATTRIB *tag* of *insert*.

    Exactly as the template's own NOTES instances do it: the ATTDEF's virtual
    MTEXT properties, placed at the ATTDEF's MTEXT insertion point transformed
    by the INSERT (origin, scale, rotation), embedded with ``embed_mtext``.
    The ATTRIB is created from the ATTDEF if the INSERT does not carry it yet.
    Its single-line (group 1) text is set to the whole body as CAD writes it -
    paragraphs joined by a space - so a reader of ``dxf.text`` sees every
    paragraph; readers should still use :func:`attrib_text` (CAD does not keep
    group 1 in step with the MTEXT).

    Raises:
        ValueError: drawing below R2018, or the ATTDEF is single-line.
        KeyError: no such ATTDEF.
    """
    from ezdxf.entities import MText

    doc = insert.doc
    _require_r2018(doc, "set_multiline_attrib")
    attdef = _attdef_for(insert, tag)
    if not attdef.has_embedded_mtext_entity:
        raise ValueError(f"ATTDEF {tag!r} is not a multi-line attribute")
    vm = attdef.virtual_mtext_entity()
    att = next((a for a in insert.attribs if a.dxf.tag == tag), None)
    if att is None:
        insert.add_auto_attribs({tag: ""})
        att = next(a for a in insert.attribs if a.dxf.tag == tag)
    props = {k: vm.dxf.get(k) for k in _MTEXT_PROPS if vm.dxf.hasattr(k)}
    props.setdefault("line_spacing_style", vm.dxf.get("line_spacing_style", 1))
    props.setdefault("line_spacing_factor", vm.dxf.get("line_spacing_factor", 1.0))
    m = MText.new(dxfattribs={**props, "insert": vm.dxf.insert}, doc=doc)
    m.text = text
    m.transform(insert.matrix44())
    att.embed_mtext(m)
    # ezdxf's set_mtext() writes only the FIRST paragraph as the group-1 text (and %%C
    # as a literal Ø); CAD writes the whole body there (Notes_Schedule.dxf *U3).
    att.dxf.text = _group1_text(text)
    # ezdxf's set_mtext() puts the single-line part at the MTEXT point and drops its
    # width factor; CAD keeps the ATTDEF's own single-line placement + width there
    # (BricsCAD seed ATTRIB: insert (2,-10), align (2,-8), width 0.75).  Restore it.
    mx = insert.matrix44()
    att.dxf.insert = mx.transform(attdef.dxf.insert)
    att.dxf.align_point = mx.transform(attdef.dxf.get("align_point", attdef.dxf.insert))
    for k in ("width", "oblique", "halign", "valign"):
        if attdef.dxf.hasattr(k):
            att.dxf.set(k, attdef.dxf.get(k))
    return att


# plain_mtext() turns the %% special codes into their glyphs; CAD's group-1 text keeps
# the codes (Notes_Schedule.dxf: "%%C350 BORED PIER" over an MTEXT "%%C350 ...").
_GLYPH_CODES = (("Ø", "%%C"), ("°", "%%D"), ("±", "%%P"))


def _group1_text(mtext: str) -> str:
    """The single-line (group 1) text CAD stores with a multi-line attribute.

    Measured on the CAD-written NOTES instance ``*U3`` of
    ``templates/Notes_Schedule.dxf``: the MTEXT content without its formatting
    codes, paragraphs joined by ONE space (689 characters for its 5 notes), the
    %%C / %%D / %%P special codes kept as written.
    """
    from ezdxf.tools.text import plain_mtext

    text = " ".join(plain_mtext(mtext, split=True))
    for glyph, code in _GLYPH_CODES:
        text = text.replace(glyph, code)
    return text


def attrib_text(attrib) -> str:
    """The text an ATTRIB / ATTDEF PLOTS - use it wherever attribute text is read.

    A multi-line attribute (the NOTES body, a SCHEDULE ROW DESCRIPTION, a
    multi-line title-block field) plots its embedded MTEXT; its group-1
    ``dxf.text`` is only a single-line copy, which ezdxf fills with the first
    paragraph alone and which CAD does not keep in step with the MTEXT (the
    template's NOTES ``*U3`` note 3 reads "FOSROC CONCURE HR90" in group 1 and
    "E5 INTERNAL CURE OR FOSROC CONCURE X90" in the MTEXT). So for a multi-line
    attribute this returns the embedded MTEXT without formatting codes, one line
    per paragraph (``\\n``); for a single-line one, ``dxf.text``.

    Args:
        attrib: an ezdxf ``Attrib`` or ``AttDef``.

    Returns:
        The attribute's plotted text ("" when it has none).
    """
    if attrib.has_embedded_mtext_entity:
        return attrib.plain_mtext()
    return attrib.dxf.get("text", "") or ""


_EMPTY_PARA = re.compile(r"^\s*$")


def wrapped_line_count(doc, mtext_props: dict, text: str) -> int:
    """Rendered line count of an MTEXT (ezdxf font metrics), blank paragraphs included.

    ``MTextExplode`` emits one TEXT per rendered line, but an EMPTY paragraph
    emits nothing - so counting TEXT lines alone under-counts (template note
    46A383: 27 counted vs 33 real, the 6 missing were blank paragraphs).  Here
    the count is the distinct TEXT baselines PLUS one line per empty paragraph.
    """
    from ezdxf.addons import MTextExplode
    from ezdxf.tools.text import plain_mtext

    tmp = doc.blocks.new_anonymous_block(type_char="X")
    try:
        m = tmp.add_mtext(text, dxfattribs=mtext_props)
        with MTextExplode(tmp) as x:
            x.explode(m, destroy=True)
        rendered = len({round(t.dxf.insert.y, 2) for t in tmp.query("TEXT")})
    finally:
        doc.blocks.delete_block(tmp.name, safe=False)
    paragraphs = plain_mtext(text, split=True)
    empty = sum(1 for p in paragraphs if _EMPTY_PARA.match(p))
    return rendered + empty



if __name__ == "__main__":
    import sys

    sys.exit(_main())
