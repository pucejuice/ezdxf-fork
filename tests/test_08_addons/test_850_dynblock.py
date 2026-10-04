"""Tests for ezdxf.addons.dynblock (LIB-DYNAMIC-BLOCKS-1).

Reference values:
* NOTES definition (template STR_TEMPLATE_AUG_2026.dwt, via the BricsCAD-built
  library): "Line Height" linear parameter base (70, -6.5), end (70, -11.5),
  min 5.0, increment 3.5; stretch selection = LINE A6 vertices 0+1 (bottom
  edge), LINE A7 / A8 vertex 0 (the two side lines' lower ends).
* BricsCAD V25's OWN seed instance at Line Height 12.0 (``*U12`` in the seeded
  library, annotation_lib_seeded.dxf sha1 43d707fe..., 2026-09-24): the
  bottom edge at y = -18.5, sides from y = -18.5 to -6.5, parameter state end
  (70, -18.5).  add_stretched at 12.0 must reproduce exactly that.
* Parameter end = base + (0, -distance) (Director's prototype dyn_write_proto.py).

Output for the Director's BricsCAD check is written to
tests/output/drafting/dynamic_blocks/ (path printed; run pytest -s to see it).
"""

from __future__ import annotations

import collections
import shutil
import sys
import tempfile
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


_require_fixture_data('annotation_blocks_dyn.dxf')


@pytest.fixture(autouse=True)
def _default_library(monkeypatch):
    """ezdxf ships no default library: the reference tests ran against the
    annotation library, so it is made the module default here."""
    monkeypatch.setattr(dyn, "LIBRARY_PATH", LIB)

DYNAMIC = ["NOTES", "SCHEDULE HEADER", "SCHEDULE ROW", "SECTION MARKER", "SECTION LABEL",
           "DETAIL LABEL", "PLAN LABEL", "TITLE LABEL"]
STATIC = ["NORTH POINT", "DETAIL MARKER", "A$C7F3A7EFF"]
TOL = 1e-6


def _graph_size(doc, name):
    return len(dyn.graph_nodes(doc, doc.blocks.get(name).block_record))


def _handles_in_file(path: Path) -> list[str]:
    """Every entity's own handle (group 5 / 105 right after group 0) in a DXF file."""
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    out, prev0 = [], False
    for i in range(0, len(lines) - 1, 2):
        code, value = lines[i].strip(), lines[i + 1].strip()
        if prev0 and code in ("5", "105"):
            out.append(value)
        prev0 = code == "0" and value not in ("SECTION", "ENDSEC", "EOF", "CLASS")
    return out


@pytest.fixture(scope="module")
def library():
    return ezdxf.readfile(LIB)


@pytest.fixture
def doc():
    d = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(d)
    return d


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    """Demo output for the CAD check: $EZDXF_DYNBLOCK_OUT if set, else a temp dir."""
    import os

    env = os.environ.get("EZDXF_DYNBLOCK_OUT")
    p = Path(env) if env else tmp_path_factory.mktemp("dynblock_out")
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── library ───────────────────────────────────────────────────────────


def test_library_modelspace_empty_and_carries_constants(library):
    assert len(library.modelspace()) == 0
    assert not [b.name for b in library.blocks if b.name.startswith("*U")]
    store = library.rootdict[dyn.REPDATA_KEY]
    assert set(store.keys()) == set(DYNAMIC)
    assert library.dxfversion == "AC1032"


# ── define_dynamic_blocks ─────────────────────────────────────────────


def test_define_brings_every_block_with_its_graph(doc, library, tmp_path):
    for n in DYNAMIC + STATIC:
        assert n in doc.blocks, n
    for n in DYNAMIC:
        br = doc.blocks.get(n).block_record
        assert "ACAD_ENHANCEDBLOCK" in br.get_extension_dict(), n
        assert _graph_size(doc, n) == _graph_size(library, n) > 0, n
        assert len(doc.blocks.get(n)) == len(library.blocks.get(n)), n
    for n in STATIC:
        assert _graph_size(doc, n) == 0
    # resources came across with the library's properties
    lt, ll = doc.layers.get("TEXT"), library.layers.get("TEXT")
    assert (lt.dxf.color, lt.dxf.lineweight) == (ll.dxf.color, ll.dxf.lineweight) == (178, 15)
    assert doc.styles.get("STUDIO_STD").dxf.font == "Ubuntu-Regular.ttf"
    a = doc.audit()
    assert len(a.fixes) == 0 and len(a.errors) == 0, [f.message for f in a.fixes + a.errors]

    p = tmp_path / "defined.dxf"
    doc.saveas(p)
    handles = _handles_in_file(p)
    dup = [h for h, c in collections.Counter(handles).items() if c > 1]
    assert not dup, dup
    re = ezdxf.readfile(p)
    ra = re.audit()
    assert len(ra.fixes) == 0 and len(ra.errors) == 0, [f.message for f in ra.fixes + ra.errors]
    for n in DYNAMIC:
        assert _graph_size(re, n) == _graph_size(library, n), n


def test_graph_references_point_at_the_new_entities(doc):
    """The NOTES stretch selection (331) must name entities of the NEW block."""
    param, stretches = dyn.read_linear_stretch(doc, "NOTES")
    block_handles = {e.dxf.handle for e in doc.blocks.get("NOTES")}
    sel = [h for st in stretches for h, _ in st["select"]]
    assert len(sel) == 3 and set(sel) <= block_handles


def test_define_is_idempotent(doc):
    n = len(doc.entitydb)
    assert dyn.define_dynamic_blocks(doc) == []
    assert len(doc.entitydb) == n


def test_define_subset_pulls_nested_block():
    d = ezdxf.new("R2018")
    done = dyn.define_dynamic_blocks(d, ["SECTION MARKER"])
    assert done == ["A$C7F3A7EFF", "SECTION MARKER"]
    assert "NOTES" not in d.blocks


def test_define_refuses_below_r2018():
    with pytest.raises(ValueError, match="R2018"):
        dyn.define_dynamic_blocks(ezdxf.new("R2013"))


def test_define_unknown_name():
    with pytest.raises(KeyError):
        dyn.define_dynamic_blocks(ezdxf.new("R2018"), ["NOT A BLOCK"])


# ── graph reading ─────────────────────────────────────────────────────


def test_read_notes_parameter(doc):
    param, stretches = dyn.read_linear_stretch(doc, "NOTES")
    assert param["label"] == "Line Height"
    assert param["min"] == 5.0 and param["inc"] == 3.5
    assert abs((param["end"] - param["base"]).magnitude - 5.0) < TOL
    assert len(stretches) == 1


def test_schedule_row_needs_parameter_name(doc):
    with pytest.raises(ValueError, match="Row Height"):
        dyn.read_linear_stretch(doc, "SCHEDULE ROW")
    p, st = dyn.read_linear_stretch(doc, "SCHEDULE ROW", "Column Width")
    assert p["inc"] == 1.5 and len(st) == 1
    # the Column Width action's trailing 75/76/94 tags are NOT vertex indices
    assert all(len(v) == len(set(v)) for _, v in st[0]["select"])


def test_visibility_states_read(doc):
    vis = dyn.read_visibility_states(doc, "SCHEDULE ROW")
    assert vis["default"] == "Divider"
    assert set(vis["states"]) == {"Divider", "No Divider", "No Front"}
    assert len(vis["states"]["Divider"]) == 7
    assert dyn.read_visibility_states(doc, "NOTES") is None


# ── add_stretched ─────────────────────────────────────────────────────


def _expected_lines(doc, distance):
    """Definition LINE endpoints with the stretch selection moved by (0, -(distance-5))."""
    param, stretches = dyn.read_linear_stretch(doc, "NOTES")
    d = Vec3(0, -(distance - 5.0), 0)
    sel = {h: v for st in stretches for h, v in st["select"]}
    out = []
    for e in doc.blocks.get("NOTES"):
        if e.dxftype() != "LINE":
            continue
        s, t = Vec3(e.dxf.start), Vec3(e.dxf.end)
        if 0 in sel.get(e.dxf.handle, ()):
            s += d
        if 1 in sel.get(e.dxf.handle, ()):
            t += d
        out.append((s, t))
    return out


@pytest.mark.parametrize("distance", [12.0, 78.5])
def test_add_stretched_notes(doc, distance):
    msp = doc.modelspace()
    ins, uname, param = dyn.add_stretched(doc, msp, "NOTES", (100, 200), distance,
                                          attribs={"NOTES_HEADER": "GENERAL NOTES", "NOTES": ""})
    u = doc.blocks.get(uname)
    got = [(Vec3(e.dxf.start), Vec3(e.dxf.end)) for e in u if e.dxftype() == "LINE"]
    exp = _expected_lines(doc, distance)
    assert len(got) == len(exp) == 7
    for (gs, ge), (es, ee) in zip(got, exp):
        assert gs.isclose(es, abs_tol=TOL) and ge.isclose(ee, abs_tol=TOL)
    bottom = got[4]                         # LINE A6, the stretched bottom edge
    assert abs(bottom[0].y - (-6.5 - distance)) < 1e-4 and abs(bottom[1].y - (-6.5 - distance)) < 1e-4
    # *U tag + representation
    btag = u.block_record.get_xdata("AcDbBlockRepBTag")
    assert [(t.code, t.value) for t in btag] == [(1070, 1), (1005, doc.blocks.get("NOTES").block_record.dxf.handle)]
    rep = ins.get_extension_dict()["AcDbBlockRepresentation"]
    rd = [t for s in rep["AcDbRepData"].xtags.subclasses for t in s]
    assert (340, doc.blocks.get("NOTES").block_record.dxf.handle) in [(t.code, t.value) for t in rd]
    xr = rep["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"][str(param["id"])]
    pts = [Vec3(t.value) for t in xr.tags if t.code == 10]
    assert pts[0].isclose(param["base"], abs_tol=TOL)
    assert pts[1].isclose(param["base"] + Vec3(0, -distance, 0), abs_tol=TOL)
    assert {a.dxf.tag for a in ins.attribs} == {"NOTES", "NOTES_HEADER"}


def test_add_stretched_matches_bricscad_seed_at_12(doc):
    """BricsCAD's own *U12 (Line Height 12.0): bottom edge y=-18.5, sides -18.5..-6.5."""
    _, uname, _ = dyn.add_stretched(doc, doc.modelspace(), "NOTES", (0, 0), 12.0)
    lines = [(Vec3(e.dxf.start), Vec3(e.dxf.end)) for e in doc.blocks.get(uname) if e.dxftype() == "LINE"]
    bricscad_u12 = [((0, 0), (70, 0)), ((0, -6.5), (70, -6.5)), ((0, 0), (0, -6.5)),
                    ((70, 0), (70, -6.5)), ((0, -18.5), (70, -18.5)), ((0, -18.5), (0, -6.5)),
                    ((70, -18.5), (70, -6.5))]
    for (gs, ge), (es, ee) in zip(lines, bricscad_u12):
        assert gs.isclose(Vec3(es), abs_tol=1e-4) and ge.isclose(Vec3(ee), abs_tol=1e-4)


def test_add_stretched_rejects_off_grid_and_below_min(doc):
    msp = doc.modelspace()
    with pytest.raises(ValueError, match="increment"):
        dyn.add_stretched(doc, msp, "NOTES", (0, 0), 10.0)
    with pytest.raises(ValueError, match="minimum"):
        dyn.add_stretched(doc, msp, "NOTES", (0, 0), 1.5)


def test_add_stretched_on_rotation_block_needs_parameter(doc):
    """SECTION MARKER (rotation + flip + 3 linear) is writable since
    LIB-DYNAMIC-BLOCKS-ROTATE-FLIP-VIS-1; with 3 linear parameters the label is required."""
    with pytest.raises(ValueError, match="parameter="):
        dyn.add_stretched(doc, doc.modelspace(), "SECTION MARKER", (0, 0), 10.0)
    ins, _, p = dyn.add_stretched(doc, doc.modelspace(), "SECTION MARKER", (0, 0), 10.0,
                                  parameter="Label Arm")
    assert p["label"] == "Label Arm"


def test_add_stretched_second_parameter_keeps_first_at_default(doc):
    ins, uname, p = dyn.add_stretched(doc, doc.modelspace(), "SCHEDULE ROW", (0, 0), 15.5,
                                      parameter="Row Height")
    ebd = ins.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
    params = {str(q["id"]): q for q in dyn.read_linear_parameters(doc, "SCHEDULE ROW")}
    for key, q in params.items():
        pts = [Vec3(t.value) for t in ebd[key].tags if t.code == 10]
        want = q["base"] + (q["end"] - q["base"]).normalize() * 15.5 if q["id"] == p["id"] else q["end"]
        assert pts[1].isclose(want, abs_tol=TOL), q["label"]
    assert ebd["9"].tags[-1].value == "Divider"      # visibility left at the default


# ── multi-line attribute + line count ─────────────────────────────────


def test_set_multiline_attrib_roundtrip(doc, tmp_path):
    text = "\\W0.7500;1. FIRST NOTE\\P2. SECOND NOTE"
    ins, _, _ = dyn.add_stretched(doc, doc.modelspace(), "NOTES", (1000, 0), 12.0,
                                  attribs={"NOTES_HEADER": "GENERAL NOTES", "NOTES": ""})
    att = dyn.set_multiline_attrib(ins, "NOTES", text)
    assert att.has_embedded_mtext_entity
    p = tmp_path / "ml.dxf"
    doc.saveas(p)
    re = ezdxf.readfile(p)
    ra = re.audit()
    assert len(ra.fixes) == 0, [f.message for f in ra.fixes]
    rins = re.entitydb[ins.dxf.handle]
    ratt = next(a for a in rins.attribs if a.dxf.tag == "NOTES")
    assert ratt.has_embedded_mtext_entity
    vm = ratt.virtual_mtext_entity()
    assert vm.text == text
    # placed at insert + the ATTDEF's embedded-MTEXT insertion point
    attdef = next(a for a in doc.blocks.get("NOTES").query("ATTDEF") if a.dxf.tag == "NOTES")
    offset = Vec3(attdef.virtual_mtext_entity().dxf.insert)
    assert Vec3(vm.dxf.insert).isclose(Vec3(1000, 0, 0) + offset, abs_tol=1e-6)
    assert vm.dxf.char_height == attdef.virtual_mtext_entity().dxf.char_height
    # single-line part as CAD writes it: the ATTDEF's own insert + width factor
    assert Vec3(ratt.dxf.insert).isclose(Vec3(1000, 0, 0) + Vec3(attdef.dxf.insert), abs_tol=1e-6)
    assert ratt.dxf.width == attdef.dxf.width == 0.75


def test_set_multiline_attrib_refuses_below_r2018(doc):
    ins, _, _ = dyn.add_stretched(doc, doc.modelspace(), "NOTES", (0, 0), 12.0)
    doc.dxfversion = "AC1027"
    with pytest.raises(ValueError, match="R2018"):
        dyn.set_multiline_attrib(ins, "NOTES", "X")


def test_wrapped_line_count_counts_blank_paragraphs(doc):
    props = {"char_height": 2.0, "width": 70.0, "style": "STUDIO_STD"}
    assert dyn.wrapped_line_count(doc, props, "LINE 1\\PLINE 2\\PLINE 3") == 3
    # 3 blank paragraphs: MTextExplode emits no TEXT for them
    assert dyn.wrapped_line_count(doc, props, "LINE 1\\P\\PLINE 2\\P\\P\\PLINE 3") == 6


# ── output for the Director + AutoCAD round trip ──────────────────────


def _chain_doc():
    d = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(d)
    msp = d.modelspace()
    for i, (lh, hdr, body) in enumerate(((12.0, "LINE HEIGHT 12.0", "1. A NOTE\\P2. ANOTHER NOTE"),
                                         (78.5, "LINE HEIGHT 78.5", "1. LONG NOTE"))):
        ins, _, _ = dyn.add_stretched(d, msp, "NOTES", (i * 100.0, 0.0), lh,
                                      attribs={"NOTES_HEADER": hdr, "NOTES": ""})
        dyn.set_multiline_attrib(ins, "NOTES", "\\W0.7500;" + body)
    return d


def test_write_director_output(out_dir):
    d = _chain_doc()
    p = out_dir / "dynamic_blocks_notes_test.dxf"
    d.saveas(p)
    print(f"\nDYNAMIC BLOCK TEST OUTPUT (for BricsCAD check): {p}")
    assert len(ezdxf.readfile(p).audit().fixes) == 0


@pytest.mark.skip(reason="needs AutoCAD Core Console (accoreconsole.exe) for a live DXF -> DWG -> DXF round trip; not available here - run on a machine with AutoCAD")
def test_autocad_roundtrip_keeps_enhancedblock(out_dir):
    """Live: AutoCAD keeps the dynamic NOTES block through DWG and back.  Skipped
    here (no AutoCAD); the gate twin is ..._fake_accore."""
    _check_roundtrip(_chain_doc(), keep_as=out_dir / "dynamic_blocks_notes_autocad_roundtrip.dxf")


def test_autocad_roundtrip_keeps_enhancedblock_fake_accore(monkeypatch):
    """Gate twin: the same checks on the lib's own DXF (the conversions are byte
    copies), so what AutoCAD must keep is present in what the lib writes."""
    calls = []
    _check_roundtrip(_chain_doc(), calls=calls)
    assert [c[0] for c in calls] == ["dxf_to_dwg", "dwg_to_dxf"]


def _check_roundtrip(d, keep_as=None, calls=None):
    dxf_to_dwg, dwg_to_dxf = _identity_accore(calls if calls is not None else [])

    work = Path(tempfile.mkdtemp(prefix="dynblk_"))
    try:
        src = work / "dyn_rt.dxf"
        d.saveas(src)
        dwg = dxf_to_dwg(src, work / "dyn_rt.dwg", timeout=300)
        back = dwg_to_dxf(dwg, work / "dyn_rt_back.dxf", timeout=300)
        if keep_as:
            shutil.copy2(back, keep_as)
        r = ezdxf.readfile(back)
        br = r.blocks.get("NOTES").block_record
        assert br.has_extension_dict and "ACAD_ENHANCEDBLOCK" in br.get_extension_dict()
        assert _graph_size(r, "NOTES") == _graph_size(d, "NOTES")
        # AutoCAD kept both instances as representations of NOTES
        reps = [e for e in r.modelspace().query("INSERT") if e.dxf.name.startswith("*U")]
        parents = set()
        for e in reps:
            btag = r.blocks.get(e.dxf.name).block_record.get_xdata("AcDbBlockRepBTag")
            parents.add(r.entitydb[next(t.value for t in btag if t.code == 1005)].dxf.name)
        assert len(reps) == 2 and parents == {"NOTES"}
        # Line Heights and the multi-line body (incl. the leading width code) survive
        heights, bodies = set(), set()
        for e in reps:
            ebd = e.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
            pts = [t.value for t in ebd["1"].tags if t.code == 10]
            heights.add(round(pts[0][1] - pts[1][1], 6))
            att = next(a for a in e.attribs if a.dxf.tag == "NOTES")
            assert att.has_embedded_mtext_entity
            bodies.add(att.virtual_mtext_entity().text)
        assert heights == {12.0, 78.5}
        assert bodies == {"\\W0.7500;1. A NOTE\\P2. ANOTHER NOTE", "\\W0.7500;1. LONG NOTE"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _identity_accore(calls: list):
    """The reference's fake accore: DXF -> 'DWG' -> DXF as byte copies, so the
    round-trip checks run on what ezdxf writes and reads back."""
    import shutil as _sh

    def dxf_to_dwg(src, dst, timeout=None):
        calls.append(("dxf_to_dwg", src, dst))
        _sh.copyfile(src, dst)
        return Path(dst)

    def dwg_to_dxf(src, dst, timeout=None):
        calls.append(("dwg_to_dxf", src, dst))
        _sh.copyfile(src, dst)
        return Path(dst)

    return dxf_to_dwg, dwg_to_dxf
