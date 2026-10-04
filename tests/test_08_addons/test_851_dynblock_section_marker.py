"""add_dynamic: rotation / flip / visibility / chained linear (LIB-DYNAMIC-BLOCKS-ROTATE-FLIP-VIS-1).

Ground truth: the 7 SECTION MARKER instances CAD placed in the owner's structural
template STR_TEMPLATE_AUG_2026.dwt (DXF export 2026-09-24, sha1 in the fixture),
recorded raw by tests/test_08_addons/dynblock_data/build_section_marker_truth.py:
rotations 0 / -90 / 155.3 / 245.3 / 52.6 deg, flip 0 and 1, visibility
'Marker Only' and 'Marker and Tail', one tail stretched (Arm Gap 20 -> 52.5)
and one arm shortened (Label Arm 25 -> 15).  For each, the state is read
straight from the instance's representation XRECORDs (independently of the
lib), add_dynamic is called with it, and the resulting *U block is compared to
CAD's *U entity by entity; the XRECORD state values are compared too.

The XRECORD header pair (group 70) differs by ORIGIN, not by state: AutoCAD
wrote (25, 104), the library's BricsCAD seed (33, 1).  add_dynamic copies the
library seed's constants (as add_stretched always has) and rewrites only the
state values, so the header is reported, not asserted equal.
"""

from __future__ import annotations

import json
import math
import shutil
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


_require_fixture_data('annotation_blocks_dyn.dxf', 'section_marker_truth.json')


@pytest.fixture(autouse=True)
def _default_library(monkeypatch):
    """ezdxf ships no default library: the reference tests ran against the
    annotation library, so it is made the module default here."""
    monkeypatch.setattr(dyn, "LIBRARY_PATH", LIB)

TRUTH = DATA / "section_marker_truth.json"
NAME = "SECTION MARKER"
TOL = 1e-6


def _tags(o):
    return [t for sub in o.xtags.subclasses for t in sub]


@pytest.fixture(scope="module")
def truth():
    return json.loads(TRUTH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def library():
    return ezdxf.readfile(LIB)


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    """Demo output for the CAD check: $EZDXF_DYNBLOCK_OUT if set, else a temp dir."""
    import os

    env = os.environ.get("EZDXF_DYNBLOCK_OUT")
    p = Path(env) if env else tmp_path_factory.mktemp("dynblock_out")
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── reading CAD's instance state, independently of the lib ────────────


def _params(library):
    """{node id (str): (dxftype, label, def points)} for SECTION MARKER's parameters."""
    out = {}
    for n in dyn.graph_nodes(library, library.blocks.get(NAME).block_record):
        if not n.dxftype().endswith("PARAMETER"):
            continue
        t = _tags(n)
        nid = next(x.value for x in t if x.code == 90)
        label = next((x.value for x in t if x.code == 305), None) or next(x.value for x in t if x.code == 300)
        pts = [Vec3(x.value) for x in t if x.code in (1010, 1011)]
        out[str(nid)] = (n.dxftype(), label, pts)
    return out


def _state_from_xrecords(ebd: dict, params: dict) -> dict:
    """rotation (deg, from the parameter's end point), flip bit, visibility name,
    linear lengths |end - base| - exactly as the Director measured them."""
    st = {"linear": {}}
    for key, (typ, label, dpts) in params.items():
        tags = ebd[key]
        pts = [Vec3(v) for c, v in tags if c == 10]
        if typ == "BLOCKROTATIONPARAMETER":
            a = math.degrees(math.atan2(*reversed((pts[1] - pts[0]).vec2)) -
                             math.atan2(*reversed((dpts[1] - dpts[0]).vec2)))
            st["rotation"] = a % 360.0
        elif typ == "BLOCKFLIPPARAMETER":
            st["flip"] = bool([v for c, v in tags if c == 70][-1])
        elif typ == "BLOCKVISIBILITYPARAMETER":
            st["visibility"] = next(v for c, v in tags if c == 1)
        elif typ == "BLOCKLINEARPARAMETER":
            st["linear"][label] = round((pts[1] - pts[0]).magnitude, 9)
    return st


# ── entity signatures (key geometry) ──────────────────────────────────


class _Ang(float):
    """An angle in degrees, compared modulo 360."""


def _load_raw(doc, pairs):
    from ezdxf.entities import factory
    from ezdxf.lldxf.extendedtags import ExtendedTags

    text = "".join(f"{c}\n{v}\n" for c, v in pairs)
    e = factory.load(ExtendedTags.from_text(text), doc)
    return e


def _etag(e):
    if not e.has_xdata("AcDbBlockRepETag"):
        return None
    x = e.get_xdata("AcDbBlockRepETag")
    idx = next((t.value for t in x if t.code == 1071), None)
    h = next((t.value for t in x if t.code == 1005), None)
    return (idx, "self" if h == e.dxf.handle else h)


def _sig(e, index_of: dict) -> dict:
    """Type, layer, invisible flag and the key geometry of one *U entity."""
    t, d = e.dxftype(), e.dxf
    s = {"type": t, "layer": d.layer, "invisible": d.get("invisible", 0), "etag": _etag(e)}
    if t == "LINE":
        s.update(start=Vec3(d.start), end=Vec3(d.end))
    elif t == "CIRCLE":
        s.update(center=Vec3(d.center), radius=d.radius)
    elif t == "LWPOLYLINE":
        s.update(closed=e.closed, extrusion=Vec3(d.get("extrusion", (0, 0, 1))),
                 points=[tuple(p) for p in e.get_points("xyseb")],
                 reactors=sorted(index_of.get(h, h) for h in e.get_reactors()))
    elif t == "HATCH":
        paths = []
        for p in e.paths:
            if p.type.name == "PolylinePath":
                paths.append(("poly", p.is_closed, [tuple(v) for v in p.vertices]))
            else:
                edges = []
                for g in p.edges:
                    if g.type.name == "LINE":
                        edges.append(("line", Vec3(g.start), Vec3(g.end)))
                    elif g.type.name == "ARC":
                        edges.append(("arc", Vec3(g.center), g.radius, _Ang(g.start_angle),
                                      _Ang(g.end_angle), g.ccw))
                    else:
                        edges.append((g.type.name,))
                paths.append(("edges", edges))
            paths.append(("assoc", sorted(index_of.get(h, h) for h in p.source_boundary_objects)))
        s.update(extrusion=Vec3(d.extrusion), solid=d.solid_fill, paths=paths,
                 seeds=[Vec3(v) for v in e.seeds])
    elif t == "INSERT":
        s.update(name=d.name, insert=Vec3(d.insert), xscale=d.xscale, yscale=d.yscale,
                 zscale=d.zscale, rotation=_Ang(d.rotation), extrusion=Vec3(d.extrusion))
    elif t in ("ATTDEF", "ATTRIB", "TEXT"):
        s.update(insert=Vec3(d.insert), align=Vec3(d.get("align_point", d.insert)), height=d.height,
                 rotation=_Ang(d.get("rotation", 0.0)), halign=d.get("halign", 0), valign=d.get("valign", 0),
                 tag=d.get("tag"), flags=d.get("flags"))
    elif t == "WIPEOUT":
        s.update(insert=Vec3(d.insert), u=Vec3(d.u_pixel), v=Vec3(d.v_pixel),
                 boundary=[tuple(v) for v in e.boundary_path])
    else:
        raise AssertionError(f"no signature for {t}")
    return s


def _flatten(v):
    if isinstance(v, (Vec3, tuple, list)):
        for x in v:
            yield from _flatten(x)
    else:
        yield v


def _compare(a, b, path, dev) -> list[str]:
    """Differences between signature values *a* and *b* (floats within TOL)."""
    if a is None or b is None:
        return [] if a is b else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        if set(a) != set(b):
            return [f"{path}: keys {sorted(a)} != {sorted(b)}"]
        return [m for k in a for m in _compare(a[k], b[k], f"{path}.{k}", dev)]
    if isinstance(a, (Vec3, tuple, list)):
        la, lb = list(a), list(b)
        if len(la) != len(lb):
            return [f"{path}: length {len(la)} != {len(lb)}"]
        return [m for i, (x, y) in enumerate(zip(la, lb)) for m in _compare(x, y, f"{path}[{i}]", dev)]
    if isinstance(a, _Ang) or isinstance(b, _Ang):
        diff = abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)
        dev["deg"] = max(dev["deg"], diff)
        return [] if diff <= TOL else [f"{path}: angle {float(a)!r} != {float(b)!r}"]
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, bool) or isinstance(b, bool):
            return [] if a == b else [f"{path}: {a!r} != {b!r}"]
        diff = abs(float(a) - float(b))
        dev["mm"] = max(dev["mm"], diff)
        return [] if diff <= TOL else [f"{path}: {a!r} != {b!r}"]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _u_signatures_cad(doc, pairs_list):
    ents = [_load_raw(doc, p) for p in pairs_list]
    index_of = {e.dxf.handle: i for i, e in enumerate(ents)}
    return [_sig(e, index_of) for e in ents]


def _u_signatures(block):
    ents = list(block)
    index_of = {e.dxf.handle: i for i, e in enumerate(ents)}
    return [_sig(e, index_of) for e in ents]


def _attrib_signature(ins_point, attribs):
    """ATTRIB placement relative to the INSERT point.

    For aligned text (halign/valign not 0) the ALIGNMENT point (group 11) places
    the text; group 10 is derived by CAD from its font metrics and the actual
    string (template AF4C29 REF_PAGE centred 'S-..': group 10 x -4.19 vs -0.76 for
    '-'), so only the governing point is compared."""
    out = {}
    for a in attribs:
        d = a.dxf
        aligned = d.get("halign", 0) or d.get("valign", 0)
        where = Vec3(d.get("align_point", d.insert)) if aligned else Vec3(d.insert)
        out[d.tag] = {"placed_at": where - ins_point, "aligned": bool(aligned),
                      "invisible": d.get("invisible", 0), "text": d.text}
    return out


def _compare_xrecords(cad: dict, ours: dict, dev) -> tuple[list[str], set]:
    """State tags (after the 4-tag header) must match; header pairs reported."""
    msgs, headers = [], set()
    if set(cad) != set(ours):
        return [f"xrecord keys {sorted(cad)} != {sorted(ours)}"], headers
    for key in cad:
        c = [tuple(t) for t in cad[key]]
        o = [(t.code, tuple(t.value) if isinstance(t.value, tuple) else t.value) for t in ours[key].tags]
        if [x[0] for x in c] != [x[0] for x in o]:
            msgs.append(f"xrecord {key}: codes {[x[0] for x in c]} != {[x[0] for x in o]}")
            continue
        if c[:2] != o[:2]:
            msgs.append(f"xrecord {key}: 1071 constants {c[:2]} != {o[:2]}")
        headers.add((tuple(v for _, v in c[2:4]), tuple(v for _, v in o[2:4])))
        for i, (x, y) in enumerate(zip(c[4:], o[4:])):
            msgs += _compare(x[1], y[1], f"xrecord {key}[{i + 4}]", dev)
    return msgs, headers


# ── the ground-truth test ─────────────────────────────────────────────


def _place(doc, inst, st):
    ins, uname, _ = dyn.add_dynamic(
        doc, doc.modelspace(), NAME, inst["insert"], rotation_deg=st["rotation"], flip=st["flip"],
        visibility=st["visibility"], linear=st["linear"],
        attribs={next(v for c, v in a if c == 2): next(v for c, v in a if c == 1) for a in inst["attribs"]})
    return ins, uname


def test_ground_truth_section_marker_instances(truth, library):
    params = _params(library)
    scratch = ezdxf.new("R2018")
    lines, failures, headers = [], [], set()
    assert len(truth["instances"]) == 7
    for inst in truth["instances"]:
        st = _state_from_xrecords(inst["ebd"], params)
        doc = ezdxf.new("R2018")
        dyn.define_dynamic_blocks(doc, [NAME])
        ins, uname = _place(doc, inst, st)
        dev = {"mm": 0.0, "deg": 0.0}
        msgs = []
        cad = _u_signatures_cad(scratch, truth["u_blocks"][inst["u"]])
        ours = _u_signatures(doc.blocks.get(uname))
        if len(cad) != len(ours):
            msgs.append(f"{len(ours)} entities, CAD has {len(cad)}")
        for i, (c, o) in enumerate(zip(cad, ours)):
            msgs += _compare(c, o, f"entity {i} {c['type']}", dev)
        ebd = ins.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
        xm, hd = _compare_xrecords(inst["ebd"], dict(ebd.items()), dev)
        msgs += xm
        headers |= hd
        cad_att = _attrib_signature(Vec3(inst["insert"]),
                                    [_load_raw(scratch, a) for a in inst["attribs"]])
        msgs += _compare(cad_att, _attrib_signature(Vec3(inst["insert"]), ins.attribs), "attribs", dev)
        hidden = sum(1 for e in doc.blocks.get(uname) if e.dxf.get("invisible", 0))
        verdict = "PASS" if not msgs else "FAIL"
        lines.append(f"{verdict} {inst['handle']:<8} {inst['u']:<7} rot {st['rotation']:7.3f} flip {int(st['flip'])} "
                     f"vis {st['visibility']!r:<18} linear {st['linear']} hidden {hidden:2d} | "
                     f"max dev {dev['mm']:.2e} mm, {dev['deg']:.2e} deg")
        if msgs:
            failures.append((inst["handle"], msgs[:20]))
    print("\nGROUND TRUTH (SECTION MARKER, template instances):")
    for ln in lines:
        print("  " + ln)
    print(f"  XRECORD header (70,70) CAD vs ours: {sorted(headers)}")
    for h, msgs in failures:
        print(f"  {h} (CAD value != ours):")
        for m in msgs:
            print(f"      {m}")
    assert not failures, failures


# ── API behaviour ─────────────────────────────────────────────────────


@pytest.fixture
def doc():
    d = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(d)
    return d


def test_read_graph_section_marker_order_from_edges(doc):
    g = dyn.read_graph(doc, NAME)
    assert g["unsupported"] == []
    kinds = {p["label"]: p["kind"] for p in g["params"].values()}
    assert kinds == {"Arm Angle": "rotation", "Label Arm": "linear", "Arm Gap": "linear",
                     "Tail Arm": "linear", "Tail Direction": "flip", "Visibility State": "visibility"}
    order = g["order"]
    pos = {nid: i for i, nid in enumerate(order)}
    # rotation first (its action moves the three linear params and the flip line);
    # each linear param before its stretch, each stretch before the params it moves
    assert pos[56] < pos[61] < min(pos[63], pos[70], pos[78], pos[90])
    assert pos[63] < pos[88] < pos[70] < pos[87] < pos[78] < pos[85]
    assert pos[87] < pos[90] < pos[95]


def test_default_instance_equals_definition(doc):
    ins, uname, state = dyn.add_dynamic(doc, doc.modelspace(), NAME, (0, 0))
    parent = list(doc.blocks.get(NAME))
    u = list(doc.blocks.get(uname))
    for p, c in zip(parent, u):
        assert p.dxftype() == c.dxftype()
        assert p.dxf.get("invisible", 0) == c.dxf.get("invisible", 0)
    assert state == {"Arm Angle": 0.0, "Tail Direction": False, "Visibility State": "Marker and Tail",
                     "Label Arm": 25.0, "Arm Gap": 20.0, "Tail Arm": 10.0}
    got = dyn.read_dynamic_state(ins)
    assert got["Visibility State"] == "Marker and Tail" and got["Tail Direction"] is False
    assert got["Arm Angle"] == pytest.approx(0.0, abs=1e-9)


def test_read_dynamic_state_round_trip(doc):
    ins, _, _ = dyn.add_dynamic(doc, doc.modelspace(), NAME, (0, 0), rotation_deg=200.0, flip=True,
                                visibility="Marker w Pointer", linear={"Label Arm": 30.0, "Tail Arm": 7.5})
    got = dyn.read_dynamic_state(ins)
    assert got["Arm Angle"] == pytest.approx(200.0, abs=1e-9)
    assert got["Tail Direction"] is True and got["Visibility State"] == "Marker w Pointer"
    assert got["Label Arm"] == pytest.approx(30.0) and got["Arm Gap"] == pytest.approx(20.0)
    assert got["Tail Arm"] == pytest.approx(7.5)


def test_visibility_marker_only_hides_eleven(doc):
    _, uname, _ = dyn.add_dynamic(doc, doc.modelspace(), NAME, (0, 0), visibility="Marker Only")
    assert sum(1 for e in doc.blocks.get(uname) if e.dxf.get("invisible", 0)) == 11


def test_rejects_bad_values(doc):
    msp = doc.modelspace()
    with pytest.raises(ValueError, match="state"):
        dyn.add_dynamic(doc, msp, NAME, (0, 0), visibility="No Such State")
    with pytest.raises(ValueError, match="increment"):
        dyn.add_dynamic(doc, msp, NAME, (0, 0), linear={"Label Arm": 26.0})
    with pytest.raises(ValueError, match="minimum"):
        dyn.add_dynamic(doc, msp, NAME, (0, 0), linear={"Arm Gap": 12.5})
    with pytest.raises(ValueError, match="no linear parameter"):
        dyn.add_dynamic(doc, msp, NAME, (0, 0), linear={"Nope": 25.0})
    with pytest.raises(ValueError, match="rotation"):
        dyn.add_dynamic(doc, msp, "NOTES", (0, 0), rotation_deg=10.0)


def test_increment_grid_is_anchored_at_the_definition_value(doc):
    """DETAIL LABEL 'Line Length': definition 41.5, min 40, increment 2.5.  CAD's own
    instances sit on 41.5 + n*2.5 (template 10C99D / E2A1 / 1101A25: 49.0), not on
    40 + n*2.5 - as do all 41 template instances of the 8 blocks whose definition
    value is off the min-anchored grid (Break Line 183 + n*50 ...).

    49.0 is written (the hexagon's WIPEOUT stretch is checked against CAD's
    *U15 in tests/test_detail_label_dynamic.py)."""
    n = len(doc.entitydb)
    with pytest.raises(ValueError, match="increment"):
        dyn.add_dynamic(doc, doc.modelspace(), "DETAIL LABEL", (0, 0), linear={"Line Length": 45.0})
    assert len(doc.entitydb) == n
    ins, _, state = dyn.add_dynamic(doc, doc.modelspace(), "DETAIL LABEL", (0, 0), linear={"Line Length": 49.0})
    assert state == {"Line Length": pytest.approx(49.0)}
    assert dyn.read_dynamic_state(ins)["Line Length"] == pytest.approx(49.0)


def test_uncopyable_entity_raises_before_writing(doc):
    """SECTION LABEL's multi-line ATTDEF carries annotative context data
    (ACDB_MTEXTATTRIBUTEOBJECTCONTEXTDATA_CLASS) in its extension dict: refused
    before the *U exists (the pre-add_dynamic clone raised only after creating it)."""
    n, blocks = len(doc.entitydb), len(doc.blocks)
    with pytest.raises(NotImplementedError, match="CONTEXTDATA"):
        dyn.add_dynamic(doc, doc.modelspace(), "SECTION LABEL", (0, 0))
    assert (len(doc.entitydb), len(doc.blocks)) == (n, blocks)


def test_dependency_cycle_raises():
    """Template FRAMING_SPEC / REO_SPEC / M16 Anchor / Strip Footing: chained
    parameters whose actions move each other - no evaluation order exists."""
    with pytest.raises(NotImplementedError, match="cycle"):
        dyn._toposort([1, 2, 3], {1: {2}, 2: {3}, 3: {2}})
    assert dyn._toposort([3, 1, 2], {1: {2}}) == [3, 1, 2]


def test_unsupported_node_type_raises(doc, monkeypatch):
    real = dyn.graph_nodes

    class _Fake:
        def dxftype(self):
            return "BLOCKXYPARAMETER"

    monkeypatch.setattr(dyn, "graph_nodes", lambda d, br: real(d, br) + [_Fake()])
    with pytest.raises(NotImplementedError, match="BLOCKXYPARAMETER"):
        dyn.add_dynamic(doc, doc.modelspace(), NAME, (0, 0))
    n = len(doc.entitydb)
    with pytest.raises(NotImplementedError):
        dyn.add_stretched(doc, doc.modelspace(), "NOTES", (0, 0), 12.0)
    assert len(doc.entitydb) == n        # nothing half-written


def test_add_stretched_still_works_on_section_marker(doc):
    ins, uname, p = dyn.add_stretched(doc, doc.modelspace(), NAME, (0, 0), 30.0, parameter="Label Arm")
    assert p["label"] == "Label Arm"
    assert dyn.read_dynamic_state(ins)["Label Arm"] == pytest.approx(30.0)


def test_hatch_association_points_at_the_clone(doc):
    _, uname, _ = dyn.add_dynamic(doc, doc.modelspace(), NAME, (0, 0), rotation_deg=90.0, flip=True)
    u = doc.blocks.get(uname)
    handles = {e.dxf.handle for e in u}
    for h in u.query("HATCH"):
        for p in h.paths:
            assert set(p.source_boundary_objects) <= handles


# ── audit + output for the Director + AutoCAD round trip ──────────────


def _demo_doc():
    d = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(d, [NAME])
    msp = d.modelspace()
    states = ("Marker and Tail", "Marker Only", "Marker w Pointer")
    for i, rot in enumerate((0.0, 45.0, 90.0, 180.0, 270.0)):
        for j, flip in enumerate((False, True)):
            for k, vis in enumerate(states):
                x, y = i * 160.0, -(j * 3 + k) * 160.0
                dyn.add_dynamic(d, msp, NAME, (x, y), rotation_deg=rot, flip=flip, visibility=vis,
                                attribs={"LABEL": f"{i}{j}{k}", "REF_PAGE": f"R{int(rot)}"})
                msp.add_text(f"rot {rot:g} flip {int(flip)} {vis}", height=5.0,
                             dxfattribs={"insert": (x - 60.0, y - 70.0)})
    return d


def test_definition_untouched_by_instances(library):
    """30 rotated / flipped / hidden instances must not leak into the definition."""
    d = _demo_doc()
    dev = {"mm": 0.0, "deg": 0.0}
    got, want = _u_signatures(d.blocks.get(NAME)), _u_signatures(library.blocks.get(NAME))
    assert len(got) == len(want) == 18
    assert not _compare(want, got, "definition", dev)
    assert dev == {"mm": 0.0, "deg": 0.0}


def test_demo_audit_clean_and_written(out_dir, tmp_path):
    d = _demo_doc()
    a = d.audit()
    assert len(a.fixes) == 0 and len(a.errors) == 0, [f.message for f in a.fixes + a.errors]
    p = out_dir / "demo_section_markers.dxf"
    d.saveas(p)
    print(f"\nSECTION MARKER DEMO (for BricsCAD check): {p}")
    r = ezdxf.readfile(p)
    ra = r.audit()
    assert len(ra.fixes) == 0 and len(ra.errors) == 0, [f.message for f in ra.fixes + ra.errors]
    assert len(r.modelspace().query("INSERT")) == 30


def _state_key(st: dict) -> tuple:
    """A state dict as a hashable key: floats rounded to 1e-6 (angles mod 360)."""
    out = []
    for k, v in sorted(st.items()):
        if isinstance(v, float):
            v = round(v % 360.0, 6) % 360.0 if k == "Arm Angle" else round(v, 6)
        out.append((k, v))
    return tuple(out)


@pytest.mark.skip(reason="needs AutoCAD Core Console (accoreconsole.exe) for a live DXF -> DWG -> DXF round trip; not available here - run on a machine with AutoCAD")
def test_autocad_roundtrip_keeps_rotate_flip_visibility(out_dir):
    """Live: AutoCAD keeps every rotate / flip / visibility state through DWG and
    back.  Skipped here (no AutoCAD); the gate twin is ..._fake_accore."""
    _check_roundtrip(keep_as=out_dir / "demo_section_markers_autocad_roundtrip.dxf")


def test_autocad_roundtrip_keeps_rotate_flip_visibility_fake_accore(monkeypatch):
    """Gate twin: the same state-by-state checks on the lib's own DXF (the
    conversions are byte copies)."""
    calls = []
    _check_roundtrip(calls=calls)
    assert [c[0] for c in calls] == ["dxf_to_dwg", "dwg_to_dxf"]


def _check_roundtrip(keep_as=None, calls=None):
    dxf_to_dwg, dwg_to_dxf = _identity_accore(calls if calls is not None else [])

    d = _demo_doc()
    before = {}
    for e in d.modelspace().query("INSERT"):
        before[e.dxf.handle] = (dyn.read_dynamic_state(e), _u_signatures(d.blocks.get(e.dxf.name)))
    work = Path(tempfile.mkdtemp(prefix="dynrot_"))
    try:
        src = work / "dynrot.dxf"
        d.saveas(src)
        dwg = dxf_to_dwg(src, work / "dynrot.dwg", timeout=300)
        back = dwg_to_dxf(dwg, work / "dynrot_back.dxf", timeout=300)
        if keep_as:
            shutil.copy2(back, keep_as)
        r = ezdxf.readfile(back)
        br = r.blocks.get(NAME).block_record
        assert "ACAD_ENHANCEDBLOCK" in br.get_extension_dict()
        assert len(dyn.graph_nodes(r, br)) == len(dyn.graph_nodes(d, d.blocks.get(NAME).block_record))
        inserts = list(r.modelspace().query("INSERT"))
        assert len(inserts) == 30
        by_state = {}
        for e in inserts:
            btag = r.blocks.get(e.dxf.name).block_record.get_xdata("AcDbBlockRepBTag")
            assert r.entitydb[next(t.value for t in btag if t.code == 1005)].dxf.name == NAME
            by_state[_state_key(dyn.read_dynamic_state(e))] = _u_signatures(r.blocks.get(e.dxf.name))
        for st, sig in before.values():
            got = by_state.get(_state_key(st))
            assert got is not None, f"state {st} lost in the round trip"
            dev = {"mm": 0.0, "deg": 0.0}
            # AutoCAD LT drops the XDATA of EVERY WIPEOUT on DXF-in - the SECTION MARKER
            # definition's own WIPEOUT (transplanted, not written by add_dynamic) loses its
            # AcDbBlockRepETag too, all other entity types keep theirs (measured 2026-09-24)
            sig = [{k: v for k, v in s.items() if not (s["type"] == "WIPEOUT" and k == "etag")} for s in sig]
            got = [{k: v for k, v in s.items() if not (s["type"] == "WIPEOUT" and k == "etag")} for s in got]
            msgs = [m for i, (a, b) in enumerate(zip(sig, got)) for m in _compare(a, b, f"entity {i}", dev)]
            assert len(sig) == len(got), (st, len(sig), len(got))
            assert not msgs, (st, msgs[:10])
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
