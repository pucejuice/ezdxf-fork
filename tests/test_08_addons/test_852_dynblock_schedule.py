"""SCHEDULE ROW / SCHEDULE HEADER written dynamically (LIB-SCHEDULE-ROW-HEADER-DYNAMIC-1).

the owner, 2026-09-25: "Notes body is 2mm text with 1.5mm borders for a 5mm single
row height typical, standard on schedule rows too. Now we have the notes block
working dynamically, can we try the same for the schedule row and schedule
header blocks".

Ground truth: ``tests/test_08_addons/dynblock_data/schedule_truth.json`` - one
AutoCAD-made ``*U`` per distinct dynamic state of the 260 SCHEDULE ROW and 32
SCHEDULE HEADER instances in ``STR_TEMPLATE_AUG_2026.dwt`` (provenance:
``build_schedule_truth.py`` next to it).  Every Divider / No Divider row there
has Row Height = max(5.0, 1.5 + 3.5 x description lines).
"""
from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Vec3

from ezdxf.addons import dynblock as dyn

DATA = Path(__file__).parent / "dynblock_data"
LIB = DATA / "annotation_blocks_dyn.dxf"


def _require_fixture_data(*names: str) -> None:
    """The CAD-made fixture data is not part of the public repository; see
    dynblock_data/README.md.  Without it this module is skipped."""
    missing = [n for n in names if not (DATA / n).exists()]
    if missing:
        pytest.skip(f"CAD fixture data not in this checkout: {missing} "
                    "(see tests/test_08_addons/dynblock_data/README.md)", allow_module_level=True)


_require_fixture_data('annotation_blocks_dyn.dxf', 'schedule_truth.json')


@pytest.fixture(autouse=True)
def _default_library(monkeypatch):
    """ezdxf ships no default library: the reference tests ran against the
    annotation library, so it is made the module default here."""
    monkeypatch.setattr(dyn, "LIBRARY_PATH", LIB)

# The downstream wrapper module (notes_schedule) is not part of the fork.
ns = None

FIXTURE = DATA / "schedule_truth.json"
TRUTH = json.loads(FIXTURE.read_text(encoding="utf-8"))
TOL = 1e-6


def _load_builder():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_schedule_truth",
                                                  FIXTURE.with_name("build_schedule_truth.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_geometry = _load_builder().entity_geometry     # the fixture's own recorder


def _close(a, b) -> bool:
    return all(abs(x - y) <= TOL for x, y in zip(a, b)) and len(a) == len(b)


def _same(got: dict, want: dict) -> list:
    """Differences between two recorded entities (empty = identical to TOL)."""
    diffs = []
    for k in ("type", "invisible", "tag"):
        if got.get(k) != want.get(k):
            diffs.append(f"{k}: {got.get(k)!r} != {want.get(k)!r}")
    for k in ("points", "seeds"):
        g, w = got.get(k, []), want.get(k, [])
        if len(g) != len(w) or not all(_close(p, q) for p, q in zip(g, w)):
            diffs.append(f"{k}: {g} != {w}")
    g, w = got.get("paths", []), want.get("paths", [])
    flat = lambda ps: [c for p in ps for e in p for c in (e if isinstance(e[0], list) else [e])]
    if len(g) != len(w) or len(flat(g)) != len(flat(w)) or not all(
            _close(p, q) for p, q in zip(flat(g), flat(w))):
        diffs.append(f"paths: {g} != {w}")
    return diffs


def _new_doc():
    doc = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(doc, ["SCHEDULE ROW", "SCHEDULE HEADER"])
    return doc


def _case_id(c) -> str:
    return f"{c['block']}|" + "|".join(f"{k}={v}" for k, v in sorted(c["state"].items()))


# -- CAD truth: the engine writes what AutoCAD wrote ---------------------------


@pytest.mark.parametrize("case", TRUTH["cases"], ids=_case_id)
def test_add_dynamic_matches_autocad_u(case):
    """Every distinct CAD state of the two blocks, entity by entity (incl. the
    Column Width 19.5 instances: MARK moved with the left edge, the header's
    associative hatch following its stretched boundary, its seed moved)."""
    doc = _new_doc()
    state = case["state"]
    linear = {k: v for k, v in state.items() if k != "Visibility State"}
    ins, uname, got_state = dyn.add_dynamic(doc, doc.modelspace(), case["block"], (0, 0),
                                            linear=linear, visibility=state.get("Visibility State"))
    for k, v in state.items():
        assert got_state[k] == pytest.approx(v, abs=TOL) if isinstance(v, float) else got_state[k] == v
    got = [_geometry(e) for e in doc.blocks.get(uname)]
    want = case["entities"]
    assert len(got) == len(want)
    diffs = {i: d for i, (g, w) in enumerate(zip(got, want)) if (d := _same(g, w))}
    assert not diffs, diffs
    # the visible MARK text: where CAD put the instance's ATTRIB
    ins.add_auto_attribs({"MARK": "A1"})
    mark = next(a for a in ins.attribs if a.dxf.tag == "MARK")
    assert _close(list(mark.dxf.align_point)[:2], case["mark_align"])
    assert int(mark.dxf.get("invisible", 0)) == case["mark_invisible"]


def test_cad_row_height_rule():
    """Row Height = max(5.0, 1.5 + 3.5 n) on every Divider / No Divider CAD state
    (1..7 description lines).  'No Front' rows are legend rows sized to the symbol
    drawn in the open mark column (pod schedule 12.0 with one line), so excluded."""
    rows = [c for c in TRUTH["cases"] if c["block"] == "SCHEDULE ROW"
            and c["state"]["Visibility State"] != "No Front"]
    assert len(rows) >= 9
    for c in rows:
        n = c["description_lines"]
        assert c["state"]["Row Height"] == pytest.approx(max(5.0, 1.5 + 3.5 * n), abs=TOL), c["description"]


# -- insert_schedule_row / _header / insert_schedule -------------------------

ONE_LINE = "190x45 E13 LVL"
# wraps to exactly 3 lines in the DESCRIPTION MTEXT (STUDIO_STD, h 2, 55.75 wide) -
# the template's own NBR row text (*U637: Row Height 12.0, 3 lines)
THREE_LINES = ("NETBRACE SYSTEM INSTALLED TO MANUF. DETAILS 2400 HIGH, LENGTH AS NOTED. "
               "450L - 11.1kN, 600L - 14.9kN")
TWO_LINES = "90x35 MGP12 @ 450 CRS - 2700 MAX HEIGHT NOGS @ MID-HEIGHT"   # *U443: 8.5, 2 lines


def _doc():
    doc = ezdxf.new("R2018")
    ns.define_notes_schedule_blocks(doc)
    return doc


def _state(ins) -> dict:
    return dyn.read_dynamic_state(ins)


def _parent(ins) -> str:
    blk = ins.doc.blocks.get(ins.dxf.name)
    if blk.block_record.has_xdata("AcDbBlockRepBTag"):
        h = next(t.value for t in blk.block_record.get_xdata("AcDbBlockRepBTag") if t.code == 1005)
        return ins.doc.entitydb[h].dxf.name
    return blk.name


def _row_box(ins):
    """(xmin, xmax, ymin, ymax) of the row's visible LINEs, world units."""
    m = ins.matrix44()
    pts = [m.transform(p) for e in ins.doc.blocks.get(ins.dxf.name)
           if e.dxftype() == "LINE" and not e.dxf.get("invisible", 0) for p in (e.dxf.start, e.dxf.end)]
    return (min(p.x for p in pts), max(p.x for p in pts), min(p.y for p in pts), max(p.y for p in pts))


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_a_one_line_row_is_5mm():
    doc = _doc()
    ins = ns.insert_schedule_row(doc.paperspace(), (10.0, 200.0), mark="B1", description=ONE_LINE,
                                 upper=False)
    assert _state(ins)["Row Height"] == pytest.approx(5.0)
    assert ns.schedule_row_height(doc, ONE_LINE, upper=False) == pytest.approx(5.0)
    assert _row_box(ins)[2:] == pytest.approx((195.0, 200.0))


@pytest.mark.parametrize("text,lines", [(TWO_LINES, 2), (THREE_LINES, 3)])
@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_b_wrapped_row_stretches_on_row_height(text, lines):
    doc = _doc()
    want = 1.5 + 3.5 * lines                   # 8.5 / 12.0, as CAD's *U443 / *U637
    ins = ns.insert_schedule_row(doc.paperspace(), (0.0, 0.0), mark="NBR", description=text)
    assert _parent(ins) == ns.BLOCK_SCHEDULE_ROW and ins.dxf.name.startswith("*U")
    assert _state(ins)["Row Height"] == pytest.approx(want)
    assert ns.schedule_row_height(doc, text) == pytest.approx(want)
    assert ns.schedule_row_height(doc, text, scale=2.0) == pytest.approx(2.0 * want)
    assert _row_box(ins)[2] == pytest.approx(-want)
    # the description is still ONE multi-line attribute of the row
    att = next(a for a in ins.attribs if a.dxf.tag == "DESCRIPTION")
    assert att.has_embedded_mtext_entity and att.virtual_mtext_entity().text == text.upper()


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_c_stacked_rows_do_not_overlap():
    from ezdxf import bbox

    doc = _doc()
    psp = doc.paperspace()
    y, boxes = 250.0, []
    for mark, text in (("NBR1", THREE_LINES), ("B1", ONE_LINE), ("ST1", TWO_LINES)):
        ins = ns.insert_schedule_row(psp, (20.0, y), mark=mark, description=text)
        box = _row_box(ins)
        # the wrapped description sits inside its own row
        att = next(a for a in ins.attribs if a.dxf.tag == "DESCRIPTION")
        ext = bbox.extents([att.virtual_mtext_entity()])
        assert ext.extmin.y >= box[2] - 1e-6 and ext.extmax.y <= box[3] + 1e-6, (mark, ext, box)
        boxes.append(box)
        y -= ns.schedule_row_height(doc, text)
    for upper, lower in zip(boxes, boxes[1:]):
        assert lower[3] <= upper[2] + 1e-9          # next row's top at/below this row's bottom
        assert lower[3] == pytest.approx(upper[2])  # ...and flush
    assert [b[3] - b[2] for b in boxes] == pytest.approx([12.0, 5.0, 8.5])


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_d_header_and_rows_share_one_column_width():
    from ezdxf import bbox

    doc = _doc()
    psp = doc.paperspace()
    rows = [("A1", "N12-200 MAX CRS 20 BTM COVER"), ("BRACE-EXT12", THREE_LINES), ("", "LABEL")]
    w = ns.schedule_mark_width(doc, [m for m, _ in rows])
    assert w > ns.MARK_W_MM
    assert (w - ns.MARK_W_MM) / 1.5 == pytest.approx(round((w - ns.MARK_W_MM) / 1.5))
    hdr = ns.insert_schedule_header(psp, (0.0, 0.0), heading="MEMBER SCHEDULE", mark_width_mm=w)
    ins = [ns.insert_schedule_row(psp, (0.0, -5.0 * i), mark=m, description=d, mark_width_mm=w)
           for i, (m, d) in enumerate(rows)]
    assert _state(hdr)["Column Width"] == pytest.approx(w)
    assert all(_state(r)["Column Width"] == pytest.approx(w) for r in ins)
    # the drawn left edge stays at the insert x; the table grows to the right
    assert _row_box(ins[0])[:2] == pytest.approx((0.0, 58.0 + w), abs=TOL)
    assert _row_box(hdr)[:2] == pytest.approx((0.0, 58.0 + w), abs=TOL)
    # the longest MARK fits between the left edge and the MARK / DESCRIPTION divider
    mark = next(a for a in ins[1].attribs if a.dxf.tag == "MARK")
    ext = bbox.extents([mark])
    assert ext.extmin.x >= 1.0 - 1e-6 and ext.extmax.x <= w - 1.0 + 1e-6, ext


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_d_insert_schedule_sizes_one_width_for_all():
    doc = _doc()
    psp = doc.paperspace()
    rows = [("", "JOISTS", "No Divider"), ("FJ1", "200x45 E13 LVL @ 450 CRS"), ("RB-EXTERNAL1", TWO_LINES)]
    h = ns.insert_schedule(psp, (100.0, 280.0), heading="FLOOR FRAMING SCHEDULE", rows=rows, upper=False)
    inserts = list(psp.query("INSERT"))
    widths = {round(_state(e)["Column Width"], 9) for e in inserts}
    assert len(inserts) == 4 and len(widths) == 1 and widths.pop() > ns.MARK_W_MM
    assert h == pytest.approx(ns.BLOCK_HEIGHT_MM + 5.0 + 5.0 + 8.5)
    boxes = [_row_box(e) for e in inserts]
    assert boxes[0][3] == pytest.approx(280.0)                   # header top at the insert
    assert min(b[2] for b in boxes) == pytest.approx(280.0 - h)
    assert all(b[0] == pytest.approx(100.0) for b in boxes)      # one left edge
    label = inserts[1]
    assert _state(label)["Visibility State"] == "No Divider"
    assert next(a for a in label.attribs if a.dxf.tag == "MARK").dxf.invisible == 1


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_e_default_call_is_todays_instance():
    """No new arguments, one line: the plain INSERT of the definition - today's
    instance, i.e. what CAD writes for an unmodified dynamic block (the
    template's 16 plain SCHEDULE ROW INSERTs) - with the definition's state."""
    doc = _doc()
    before = len(doc.blocks)
    ins = ns.insert_schedule_row(doc.modelspace(), (0.0, 0.0), mark="A1", description="N12-200 BTM")
    assert ins.dxf.name == ns.BLOCK_SCHEDULE_ROW and len(doc.blocks) == before
    assert not ins.has_extension_dict
    assert _state(ins) == {"Column Width": pytest.approx(12.0), "Visibility State": "Divider",
                           "Row Height": pytest.approx(5.0)}
    hdr = ns.insert_schedule_header(doc.modelspace(), (0.0, 0.0), heading="BAR SCHEDULE")
    assert hdr.dxf.name == ns.BLOCK_SCHEDULE_HEADER and not hdr.has_extension_dict


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_f_stretched_state_survives_save_and_reload(tmp_path):
    doc = _doc()
    psp = doc.paperspace()
    hdr = ns.insert_schedule_header(psp, (0.0, 0.0), heading="SCHEDULE", mark_width_mm=15.0)
    row = ns.insert_schedule_row(psp, (0.0, 0.0), mark="NBR", description=THREE_LINES,
                                 mark_width_mm=15.0)
    handles = (hdr.dxf.handle, row.dxf.handle)
    p = tmp_path / "rows.dxf"
    doc.saveas(p)
    re = ezdxf.readfile(p)
    fixes = re.audit().fixes
    assert len(fixes) == 0, [f.message for f in fixes]
    rh, rr = (re.entitydb[h] for h in handles)
    assert dyn.read_dynamic_state(rh) == {"Column Width": pytest.approx(15.0)}
    assert dyn.read_dynamic_state(rr) == {"Column Width": pytest.approx(15.0),
                                          "Visibility State": "Divider", "Row Height": pytest.approx(12.0)}
    assert _parent(rr) == ns.BLOCK_SCHEDULE_ROW
    att = next(a for a in rr.attribs if a.dxf.tag == "DESCRIPTION")
    assert att.has_embedded_mtext_entity
    # and the schedule reader still sees the stretched row
    from studio.documents import schedule_consistency as sc
    ext = sc.extract_dxf_schedule(str(p))
    assert ext.extracted and [e.mark for e in ext.entries] == ["NBR"]


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_scale_multiplies_the_stretched_height():
    doc = _doc()
    ins = ns.insert_schedule_row(doc.modelspace(), (0.0, 0.0), mark="NBR", description=THREE_LINES,
                                 scale=50.0)
    assert _state(ins)["Row Height"] == pytest.approx(12.0)        # block units
    assert _row_box(ins)[2] == pytest.approx(-600.0)


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_bad_mark_width_and_visibility_raise():
    doc = _doc()
    psp = doc.paperspace()
    with pytest.raises(ValueError, match="increment"):
        ns.insert_schedule_row(psp, (0, 0), mark="A1", description="X", mark_width_mm=14.0)
    with pytest.raises(ValueError, match="minimum"):
        ns.insert_schedule_row(psp, (0, 0), mark="A1", description="X", mark_width_mm=10.5)
    with pytest.raises(ValueError, match="maximum"):
        ns.insert_schedule_header(psp, (0, 0), heading="H", mark_width_mm=31.5)
    with pytest.raises(ValueError, match="visibility state"):
        ns.insert_schedule_row(psp, (0, 0), mark="A1", description="X", visibility="Hidden")
    with pytest.raises(ValueError, match="maximum"):
        ns.schedule_mark_width(doc, ["M" * 30])


@pytest.mark.skip(reason="uses the downstream notes_schedule wrapper, which is not part of the fork")
def test_schedule_mark_width_default_for_template_marks():
    """The template's marks all sit in the default 12 mm column; the widest,
    'PLATES', measures 9.98 mm (CAD's own ATTRIB: 2 x 4.99) - 1 mm each side."""
    doc = _doc()
    assert ns.schedule_mark_width(doc, ["PLATES", "STUDS", "FJ1", "<1400", "A1"]) == pytest.approx(12.0)
    assert ns.schedule_mark_width(doc, []) == pytest.approx(12.0)
