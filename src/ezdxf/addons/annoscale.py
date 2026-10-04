# License: MIT
"""Annotation scales: annotative TEXT and the per-viewport annotation scale.

EXPERIMENTAL.  None of the structures written here is in the Autodesk DXF
reference.  They were ported from a downstream drafting library, whose own notes
say the object graph was decoded from a controlled BricsCAD round trip (two TEXT
entities made annotative in CAD) and that the direction written here (generated
DXF read by CAD) still awaits live CAD confirmation.

What was checked against CAD-made files in ``tests/test_08_addons/dynblock_data``
(an AutoCAD-saved drawing and BricsCAD-made block libraries):

- the ``AcadAnnotative`` XDATA, tag for tag (STYLE / DIMSTYLE / ATTDEF);
- the extension-dictionary chain ``AcDbContextDataManager`` ->
  ``ACDB_ANNOTATIONSCALES`` -> ``*A<n>`` entries, and that CAD writes both
  dictionaries without the hard-owner flag (group 280);
- the common part of a context-data object (``AcDbObjectContextData`` /
  ``AcDbAnnotScaleObjectContextData``), on BricsCAD's MTEXT-attribute variant;
- the SCALE record layout and the ``ACAD_SCALELIST`` keys ``A0``, ``A1``, ...

What has NO CAD-made reference here: the TEXT context-data class itself
(``ACDB_TEXTOBJECTCONTEXTDATA_CLASS``), annotative MTEXT (CAD uses a different
class with a different layout - not written, raises), and the viewport record
``ASDK_XREC_ANNOTATION_SCALE_INFO``.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterable, Optional

from ezdxf.entities import Dictionary, XRecord
from ezdxf.lldxf.const import DXFKeyError

if TYPE_CHECKING:
    from ezdxf.document import Drawing
    from ezdxf.entities import DXFEntity, Viewport

__all__ = [
    "ensure_annotation_scale",
    "find_annotation_scale",
    "make_annotative",
    "annotation_scale_name",
    "viewport_scale_name",
    "set_viewport_annotation_scale",
    "VIEWPORT_ANNO_SCALE_XREC",
]

SCALELIST_KEY = "ACAD_SCALELIST"
SCALE_TYPE = "SCALE"
CTX_TYPE = "ACDB_TEXTOBJECTCONTEXTDATA_CLASS"
CDM_KEY = "AcDbContextDataManager"
ANNOSCALES_KEY = "ACDB_ANNOTATIONSCALES"
ANNO_APPID = "AcadAnnotative"
# As CAD writes it for an annotative object (second 1070 = 1; CAD writes 0 on
# objects that are not annotative):
ANNO_XDATA_TAGS = (
    (1000, "AnnotativeData"),
    (1002, "{"),
    (1070, 1),
    (1070, 1),
    (1002, "}"),
)
#: Extension-dictionary XRECORD of a VIEWPORT holding its annotation scale.
VIEWPORT_ANNO_SCALE_XREC = "ASDK_XREC_ANNOTATION_SCALE_INFO"


# ── SCALE records ─────────────────────────────────────────────────────


def _scale_paper_drawing(name: str) -> tuple[float, float]:
    """``'paper:drawing'`` -> (paper, drawing) units; ``'1:20'`` -> (1.0, 20.0)."""
    try:
        paper_s, drawing_s = str(name).split(":")
        paper, drawing = float(paper_s), float(drawing_s)
    except (ValueError, AttributeError):
        raise ValueError(
            f"malformed annotation-scale name {name!r}; expected 'paper:drawing', "
            f"e.g. '1:20'"
        ) from None
    if not (paper > 0 and drawing > 0 and math.isfinite(paper) and math.isfinite(drawing)):
        raise ValueError(f"annotation-scale {name!r} must have positive paper/drawing units")
    return paper, drawing


def _scale_name(scale: DXFEntity) -> Optional[str]:
    # SCALE is not modelled by ezdxf: the name is group code 300
    for subclass in scale.xtags.subclasses:  # type: ignore[attr-defined]
        for tag in subclass:
            if tag.code == 300:
                return tag.value
    return None


def _scalelist(doc: Drawing) -> Dictionary:
    scalelist = doc.rootdict.get(SCALELIST_KEY)
    if scalelist is None:
        scalelist = doc.rootdict.add_new_dict(SCALELIST_KEY)
    if not isinstance(scalelist, Dictionary):
        raise TypeError(f"{SCALELIST_KEY} is a {scalelist.dxftype()}, not a DICTIONARY")
    return scalelist


def find_annotation_scale(doc: Drawing, name: str) -> Optional[str]:
    """Handle of the SCALE record called `name` in ``ACAD_SCALELIST``, or ``None``.

    Scales are looked up by their NAME (group code 300): CAD keys the list
    ``A0``, ``A1``, ... not by name.
    """
    scalelist = doc.rootdict.get(SCALELIST_KEY)
    if not isinstance(scalelist, Dictionary):
        return None
    for _, scale in scalelist.items():
        if scale.dxftype() == SCALE_TYPE and _scale_name(scale) == name:
            return scale.dxf.handle
    return None


def ensure_annotation_scale(doc: Drawing, name: str) -> str:
    """Handle of the SCALE record `name` (``'paper:drawing'``, e.g. ``'1:20'``),
    created in ``ACAD_SCALELIST`` if absent.  Idempotent.

    A new record is written as CAD writes one: ``AcDbScale`` 70 0, 300 name,
    140 paper units, 141 drawing units, 290 unit-scale flag, under the next
    free key ``A<n>``.

    Raises:
        ValueError: malformed `name`
    """
    paper, drawing = _scale_paper_drawing(name)
    handle = find_annotation_scale(doc, name)
    if handle is not None:
        return handle
    from ezdxf.entities import DXFTagStorage, factory

    scalelist = _scalelist(doc)
    is_unit = 1 if paper == drawing else 0
    text = (
        f"0\n{SCALE_TYPE}\n100\nAcDbScale\n70\n0\n300\n{name}\n"
        f"140\n{paper}\n141\n{drawing}\n290\n{is_unit}\n"
    )
    obj = DXFTagStorage.from_text(text, doc)
    factory.bind(obj, doc)
    doc.objects.add_object(obj)  # type: ignore[arg-type]
    obj.dxf.owner = scalelist.dxf.handle
    i = len(scalelist)
    while f"A{i}" in scalelist:
        i += 1
    scalelist.add(f"A{i}", obj)  # type: ignore[arg-type]
    return obj.dxf.handle


# ── annotative TEXT ───────────────────────────────────────────────────


def _ensure_contextdata_class(doc: Drawing) -> None:
    """Register the CLASS of the context-data objects (idempotent).  Flags 1153
    as CAD writes them for the context-data class it does write (BricsCAD,
    ACDB_MTEXTATTRIBUTEOBJECTCONTEXTDATA_CLASS) and as ezdxf writes SCALE."""
    from ezdxf.entities import DXFClass

    try:
        doc.classes.get(CTX_TYPE)
        return
    except DXFKeyError:
        pass
    cls = DXFClass.new(doc=doc)
    cls.update_dxf_attribs(
        {
            "name": CTX_TYPE,
            "cpp_class_name": "AcDbTextObjectContextData",
            "app_name": "ObjectDBX Classes",
            "flags": 1153,
            "was_a_proxy": 0,
            "is_an_entity": 0,
        }
    )
    doc.classes.register(cls)


def _add_context_data(doc: Drawing, owner: str, scale: str, is_default: bool, insert,
                      align, rotation: float):
    from ezdxf.entities import DXFTagStorage, factory

    ix, iy = float(insert[0]), float(insert[1])
    ax, ay = float(align[0]), float(align[1])
    text = (
        f"0\n{CTX_TYPE}\n"
        f"100\nAcDbObjectContextData\n70\n4\n290\n{1 if is_default else 0}\n"
        f"100\nAcDbAnnotScaleObjectContextData\n340\n{scale}\n70\n0\n"
        f"50\n{float(rotation)}\n"
        f"10\n{ix}\n20\n{iy}\n30\n0.0\n"
        f"11\n{ax}\n21\n{ay}\n31\n0.0\n"
    )
    obj = DXFTagStorage.from_text(text, doc)
    factory.bind(obj, doc)
    doc.objects.add_object(obj)  # type: ignore[arg-type]
    obj.dxf.owner = owner
    return obj


def make_annotative(entity: DXFEntity, scales: Iterable[str],
                    default_scale: Optional[str] = None) -> DXFEntity:
    """Make a TEXT entity annotative at the annotation `scales` (EXPERIMENTAL).

    Writes the ``AcadAnnotative`` XDATA and, in the entity's extension
    dictionary, ``AcDbContextDataManager / ACDB_ANNOTATIONSCALES`` with one
    ``ACDB_TEXTOBJECTCONTEXTDATA_CLASS`` object per scale (``*A1``, ``*A2``,
    ...); each points (340) at the scale's SCALE record and carries the text's
    insertion / alignment point and rotation.  Exactly one scale is the
    default.  Idempotent: a repeated call rebuilds the context data.

    Args:
        entity: TEXT bound to a document
        scales: scale names ``'paper:drawing'``, duplicates collapsed; missing
            SCALE records are created (:func:`ensure_annotation_scale`)
        default_scale: one of `scales`, default the first

    Raises:
        ValueError: unbound entity, empty `scales`, malformed name, or
            `default_scale` not in `scales`
        NotImplementedError: MTEXT (CAD uses a different context-data class
            for MTEXT and no CAD-made reference is available) or any other type
    """
    doc = entity.doc
    if doc is None:
        raise ValueError("make_annotative: entity is not bound to a document")
    dxftype = entity.dxftype()
    if dxftype == "MTEXT":
        raise NotImplementedError(
            "make_annotative: annotative MTEXT is not implemented - CAD writes a "
            "different context-data class for MTEXT and no CAD-made reference is "
            "available"
        )
    if dxftype != "TEXT":
        raise NotImplementedError(f"make_annotative supports TEXT, not {dxftype!r}")
    names = list(dict.fromkeys(scales))
    if not names:
        raise ValueError("make_annotative: scales must not be empty")
    for name in names:
        _scale_paper_drawing(name)
    if default_scale is None:
        default_scale = names[0]
    elif default_scale not in names:
        raise ValueError(
            f"make_annotative: default_scale {default_scale!r} is not one of {names}"
        )

    insert = entity.dxf.insert
    rotation = float(entity.dxf.get("rotation", 0.0))
    align = entity.dxf.get("align_point", None) or insert

    _ensure_contextdata_class(doc)
    if ANNO_APPID not in doc.appids:
        doc.appids.add(ANNO_APPID)
    entity.set_xdata(ANNO_APPID, list(ANNO_XDATA_TAGS))

    xdict = entity.get_extension_dict() if entity.has_extension_dict else entity.new_extension_dict()
    xd = xdict.dictionary
    # CAD writes both dictionaries without the hard-owner flag (group 280)
    cdm = xd[CDM_KEY] if CDM_KEY in xd else xd.add_new_dict(CDM_KEY)
    if not isinstance(cdm, Dictionary):
        raise TypeError(f"{CDM_KEY} of {entity} is a {cdm.dxftype()}, not a DICTIONARY")
    if ANNOSCALES_KEY in cdm:
        annoscales = cdm[ANNOSCALES_KEY]
        if not isinstance(annoscales, Dictionary):
            raise TypeError(f"{ANNOSCALES_KEY} of {entity} is not a DICTIONARY")
        for key in list(annoscales.keys()):
            obj = annoscales.get(key)
            annoscales.discard(key)
            if obj is not None and obj.is_alive:
                doc.objects.delete_entity(obj)  # type: ignore[arg-type]
    else:
        annoscales = cdm.add_new_dict(ANNOSCALES_KEY)
    owner = annoscales.dxf.handle
    for i, name in enumerate(names):
        obj = _add_context_data(
            doc, owner, ensure_annotation_scale(doc, name), name == default_scale,
            insert, align, rotation,
        )
        annoscales.add(f"*A{i + 1}", obj)
    return entity


# ── viewport annotation scale ─────────────────────────────────────────


def _fmt_ratio(x: float) -> str:
    """A scale ratio as CAD names it: 20.0 -> '20', 2.5 -> '2.5' (6 decimals)."""
    text = f"{round(x, 6):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def annotation_scale_name(view_height: float, height: float) -> str:
    """CAD name of a view scale: ``'1:20'``, or ``'2:1'`` for an enlargement.

    The view scale is `view_height` / `height` (drawing units per paper unit).

    Raises:
        ValueError: non-positive `height` or `view_height`
    """
    h, vh = float(height), float(view_height)
    if h <= 0.0 or vh <= 0.0:
        raise ValueError(
            f"viewport height {h:g} / view_height {vh:g}; no view scale to set an "
            "annotation scale from"
        )
    s = vh / h
    if s >= 1.0 or _fmt_ratio(s) == "1":
        return f"1:{_fmt_ratio(s)}"
    return f"{_fmt_ratio(1.0 / s)}:1"


def viewport_scale_name(vp: Viewport) -> str:
    """:func:`annotation_scale_name` of a VIEWPORT's own geometry."""
    return annotation_scale_name(vp.dxf.view_height, vp.dxf.height)


def set_viewport_annotation_scale(vp: Viewport) -> str:
    """Set the viewport's annotation scale to its view scale and return the
    scale name (EXPERIMENTAL; no CAD-made reference for this record is available).

    The SCALE record is found by name in ``ACAD_SCALELIST`` or added there; the
    viewport's ``ASDK_XREC_ANNOTATION_SCALE_INFO`` XRECORD is set to
    ``(90, 1), (340, scale handle)``.  Idempotent; call again after changing
    ``view_height`` or ``height``.

    Raises:
        ValueError: degenerate viewport
    """
    doc = vp.doc
    if doc is None:
        raise ValueError("set_viewport_annotation_scale: viewport is not bound to a document")
    name = viewport_scale_name(vp)
    handle = ensure_annotation_scale(doc, name)
    xdict = vp.get_extension_dict() if vp.has_extension_dict else vp.new_extension_dict()
    xrec = xdict.get(VIEWPORT_ANNO_SCALE_XREC)
    if xrec is None:
        xrec = xdict.add_xrecord(VIEWPORT_ANNO_SCALE_XREC)
    if not isinstance(xrec, XRecord):
        raise TypeError(f"{VIEWPORT_ANNO_SCALE_XREC} of {vp} is not an XRECORD")
    xrec.reset([(90, 1), (340, handle)])
    return name
