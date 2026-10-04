"""MASONRY BLOCK: the owner's reinforced masonry dynamic block (LIB-DRAFTING-MASONRY-BLOCK-DYN-1).

Ground truth: the 6 instances CAD placed in the owner's source drawing
``Reinforced Masonry Block.dxf`` (2026-10-02; committed as
``tests/test_08_addons/dynblock_data/reinforced_masonry_block.dxf`` (not supplied yet), content
sha1 pinned below): ``*U3`` '200 Full' (AutoCAD-written), ``*U6`` / ``*U10`` '200
Section' (``*U10`` with Height 95), ``*U7`` '200 Capped', ``*U8`` '300 Section',
``*U9`` '200 290D Lintel' (BricsCAD-written).  For each, the state is read
straight from the instance's representation XRECORDs (independently of the lib),
``add_masonry_block`` is called with it, and the resulting ``*U`` block is
compared with CAD's entity by entity; the XRECORD state values are compared too.

What a HATCH is compared on, and why not its stored boundary: CAD REGENERATES an
associative hatch's boundary from its boundary objects whenever it writes a
``*U``.  AutoCAD keeps edge paths (``*U3``); BricsCAD rewrites them as polyline
paths split into islands (``*U6`` hatch on 101 / 102: 2 edge paths in the
definition, 8 polyline paths in CAD's ``*U``), so the path LIST is
CAD-specific.  What both agree on - and what plots - is the REGION the hatch
fills.  So a hatch is compared on: layer, invisible flag, pattern name / scale /
angle, the boundary objects it is associated with (by entity index), and its
filled region, measured exactly along scanlines 0.5 mm apart.  A SHOWN hatch's region is read
even-odd, as it plots.  A hidden hatch in a D = 0 state has a FOLDED boundary
(the D stretch moves the block's mid-height vertices below its base): CAD
regenerates it into non-overlapping islands whose region is the NONZERO-winding
region of the folded boundary (every hidden hatch of the six instances, measured
2026-10-02; even-odd differs by up to 8550 mm2), so hidden hatches are read nonzero.  The
lib keeps the folded boundary (it never plots - the hatch is hidden in every
state that folds it).  Seed points and the pattern
origin are reported, not asserted: they are history-dependent in CAD's ``*U`` -
``*U6`` has W at its definition value 190 yet carries the +200 seed / pattern
origin of a W = 390 state it was edited through (hatch on 10B: seed (200, 0)).
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf.math import Vec3

from ezdxf.addons import dynblock as dyn

DATA = Path(__file__).parent / "dynblock_data"


class mb:
    """Minimal stand-in for the downstream ``masonry_blocks`` wrapper: block name,
    library, and the keyword -> parameter mapping of ``add_masonry_block``.  House
    rules of the wrapper (INSERT layer, colours, default entry, listing) are NOT
    reproduced; the tests of those are skipped."""

    BLOCK_NAME = "MASONRY BLOCK"
    LIBRARY_PATH = DATA / "masonry_blocks_dyn.dxf"
    __file__ = str(DATA / "masonry_blocks.py")      # SRC is looked up beside it

    @classmethod
    def define_masonry_block(cls, doc):
        return dyn.define_dynamic_blocks(doc, [cls.BLOCK_NAME], library=cls.LIBRARY_PATH)

    @classmethod
    def add_masonry_block(cls, doc, layout, insert, *, block=None, visibility=None, width=None,
                          depth=None, height=None):
        cls.define_masonry_block(doc)
        linear = {k: v for k, v in (("W", width), ("D", depth), ("Height", height)) if v is not None}
        return dyn.add_dynamic(doc, layout, cls.BLOCK_NAME, insert, lookup=block, visibility=visibility,
                               linear=linear or None, library=cls.LIBRARY_PATH)


SRC_MISSING = "needs the CAD source drawing reinforced_masonry_block.dxf (not supplied) in dynblock_data/"

SRC = DATA / "reinforced_masonry_block.dxf"
SRC_SHA1 = "48229714a7556d76abdc970cf3b2f24ce8557b45"   # the owner's file as received, CRLF
SRC_NAME = "Block"
TOL = 1e-6
GRID = 0.5          # mm, scanline step of the hatch-region comparison
REGION_TOL = 0.5    # mm2, hatch-region symmetric difference allowed


def _tags(o):
    return [t for sub in o.xtags.subclasses for t in sub]


@pytest.fixture(scope="module")
def src():
    if not SRC.exists():
        pytest.skip(SRC_MISSING)
    return ezdxf.readfile(SRC)


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    import os

    env = os.environ.get("EZDXF_DYNBLOCK_OUT")
    p = Path(env) if env else tmp_path_factory.mktemp("dynblock_out")
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.skipif(not SRC.exists(), reason=SRC_MISSING)
def test_source_is_the_owners_file():
    data = SRC.read_bytes()
    crlf = data if b"\r\n" in data else data.replace(b"\n", b"\r\n")    # git may check it out LF
    assert hashlib.sha1(crlf).hexdigest() == SRC_SHA1


# ── reading CAD's instance state, independently of the lib ────────────


def _param_labels(doc, name):
    """{node id (str): (dxftype, label)} - label 305 (linear), 303 (lookup), else 300."""
    out = {}
    for n in dyn.graph_nodes(doc, doc.blocks.get(name).block_record):
        if not n.dxftype().endswith("PARAMETER"):
            continue
        t = _tags(n)
        nid = next(x.value for x in t if x.code == 90)
        if n.dxftype() == "BLOCKLOOKUPPARAMETER":
            label = next(x.value for x in t if x.code == 303)
        else:
            label = next((x.value for x in t if x.code == 305 and x.value), None) or \
                next(x.value for x in t if x.code == 300)
        out[str(nid)] = (n.dxftype(), label)
    return out


def _ebd(insert):
    return insert.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]


def _cad_state(insert, labels):
    ebd = _ebd(insert)
    st = {}
    for key, (typ, label) in labels.items():
        tags = list(ebd[key].tags)
        if typ == "BLOCKLINEARPARAMETER":
            pts = [Vec3(t.value) for t in tags if t.code == 10]
            st[label] = round((pts[1] - pts[0]).magnitude, 9)
        else:
            st[label] = next(t.value for t in tags if t.code == 1)
    return st


def _instances(src):
    labels = _param_labels(src, SRC_NAME)
    out = []
    for e in src.modelspace().query("INSERT"):
        out.append({"handle": e.dxf.handle, "u": e.dxf.name, "insert": Vec3(e.dxf.insert),
                    "state": _cad_state(e, labels), "ebd": {k: list(x.tags) for k, x in _ebd(e).items()}})
    return out


# ── entity signatures ─────────────────────────────────────────────────


def _etag(e):
    """The AcDbBlockRepETag index (group 1071).  Its handle (1005) is reported,
    not compared: AutoCAD points it at the entity itself (``*U3``, as the lib
    writes it), BricsCAD at the same entity of the drawing's first ``*U`` (its
    ``*U6`` LINE -> ``*U3`` LINE 12F) - a CAD-origin difference, like the XRECORD
    header."""
    if not e.has_xdata("AcDbBlockRepETag"):
        return None
    return next((t.value for t in e.get_xdata("AcDbBlockRepETag") if t.code == 1071), None)


def _sig(e, index_of):
    t, d = e.dxftype(), e.dxf
    s = {"type": t, "layer": d.layer, "invisible": d.get("invisible", 0), "etag": _etag(e),
         "linetype": d.get("linetype", "BYLAYER").upper()}
    if t == "LINE":
        s.update(start=Vec3(d.start), end=Vec3(d.end))
    elif t == "LWPOLYLINE":
        s.update(closed=e.closed, points=[tuple(float(v) for v in p) for p in e.get_points("xyseb")],
                 reactors=sorted(index_of.get(h, h) for h in e.get_reactors()))
    elif t == "HATCH":
        s.update(pattern=(d.pattern_name, d.solid_fill, round(d.get("pattern_scale", 1.0), 9),
                          round(d.get("pattern_angle", 0.0), 9), d.get("associative", 0)),
                 sources=sorted({index_of.get(h, h) for p in e.paths for h in p.source_boundary_objects}))
    else:
        raise AssertionError(f"no signature for {t}")
    return s


def _compare(a, b, path, dev):
    if isinstance(a, dict):
        if set(a) != set(b):
            return [f"{path}: keys {sorted(a)} != {sorted(b)}"]
        return [m for k in a for m in _compare(a[k], b[k], f"{path}.{k}", dev)]
    if isinstance(a, (Vec3, tuple, list)) and isinstance(b, (Vec3, tuple, list)):
        la, lb = list(a), list(b)
        if len(la) != len(lb):
            return [f"{path}: length {len(la)} != {len(lb)}"]
        return [m for i, (x, y) in enumerate(zip(la, lb)) for m in _compare(x, y, f"{path}[{i}]", dev)]
    if isinstance(a, float) or isinstance(b, float):
        diff = abs(float(a) - float(b))
        dev["mm"] = max(dev["mm"], diff)
        return [] if diff <= TOL else [f"{path}: {a!r} != {b!r}"]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _loops(h):
    """The hatch's boundary loops as flattened (n, 2) arrays."""
    from ezdxf import path as ezpath

    out = []
    for p in ezpath.from_hatch(h):
        for sub in p.sub_paths():
            v = np.array([(q.x, q.y) for q in sub.flattening(0.005)])
            if len(v) >= 3:
                out.append(v)
    return out


def _row_intervals(loops, ys, rule):
    """Per scanline y: the x intervals inside the region of *loops* under *rule*
    ('evenodd' / 'nonzero'), from the exact edge crossings (half-open in y)."""
    v0 = np.vstack(loops)
    v1 = np.vstack([np.roll(v, -1, axis=0) for v in loops])
    x0, y0, x1, y1 = v0[:, 0], v0[:, 1], v1[:, 0], v1[:, 1]
    Y = ys[:, None]
    up = (y0 <= Y) & (y1 > Y)
    dn = (y1 <= Y) & (y0 > Y)
    dy = np.where(y1 == y0, 1.0, y1 - y0)
    X = x0 + (Y - y0) * (x1 - x0) / dy
    out = []
    for r in range(len(ys)):
        ev = sorted([(x, 1) for x in X[r][up[r]]] + [(x, -1) for x in X[r][dn[r]]])
        iv, w = [], 0
        for i, (x, s) in enumerate(ev[:-1]):
            w = w + s if rule == "nonzero" else w ^ 1
            if w != 0:
                iv.append((x, ev[i + 1][0]))
        out.append(iv)
    return out


def _sym_diff_length(a, b):
    """Length of the symmetric difference of two interval lists on one line."""
    pts = sorted({p for iv in (a, b) for seg in iv for p in seg})

    def inside(iv, m):
        return any(lo <= m <= hi for lo, hi in iv)

    return sum(q - p for p, q in zip(pts, pts[1:]) if inside(a, (p + q) / 2) != inside(b, (p + q) / 2))


def _region_diff(la, rule_a, lb, rule_b):
    """(area of the symmetric difference, area of region a), mm2: exact along x,
    scanlines every GRID mm in y (offset off the vertices)."""
    allv = np.vstack(la + lb)
    ys = np.arange(allv[:, 1].min() + 0.0713, allv[:, 1].max(), GRID)
    ra, rb = _row_intervals(la, ys, rule_a), _row_intervals(lb, ys, rule_b)
    diff = sum(_sym_diff_length(a, b) for a, b in zip(ra, rb)) * GRID
    return diff, sum(hi - lo for row in ra for lo, hi in row) * GRID


def _region_mismatch(cad_h, our_h, rule):
    """(mismatched area mm2, CAD area mm2) between two hatches' filled regions.

    CAD's own boundary is always read even-odd (its regenerated loops do not
    overlap); ours with *rule* ('evenodd' / 'nonzero')."""
    return _region_diff(_loops(cad_h), "evenodd", _loops(our_h), rule)


def _signatures(block):
    ents = list(block)
    index_of = {e.dxf.handle: i for i, e in enumerate(ents)}
    return ents, [_sig(e, index_of) for e in ents]


# ── the ground-truth test ─────────────────────────────────────────────


def _place(doc, inst):
    st = inst["state"]
    return mb.add_masonry_block(doc, doc.modelspace(), inst["insert"], block=st["Block"],
                                visibility=st["Visibility State"], width=st["W"], depth=st["D"],
                                height=st["Height"])


def test_ground_truth_masonry_instances(src):
    insts = _instances(src)
    assert sorted(i["u"] for i in insts) == ["*U10", "*U3", "*U6", "*U7", "*U8", "*U9"]
    lines, failures = [], []
    for inst in insts:
        doc = ezdxf.new("R2018")
        ins, uname, state = _place(doc, inst)
        dev = {"mm": 0.0}
        msgs, region = [], []
        cad_ents, cad = _signatures(src.blocks.get(inst["u"]))
        our_ents, ours = _signatures(doc.blocks.get(uname))
        if len(cad) != len(ours):
            msgs.append(f"{len(ours)} entities, CAD has {len(cad)}")
        for i, (c, o) in enumerate(zip(cad, ours)):
            msgs += _compare(c, o, f"entity {i} {c['type']}", dev)
            if c["type"] == "HATCH":
                bad, area = _region_mismatch(cad_ents[i], our_ents[i], "nonzero" if c["invisible"] else "evenodd")
                region.append(bad)
                if bad > REGION_TOL:
                    msgs.append(f"entity {i} HATCH ({'hidden' if c['invisible'] else 'shown'}): region "
                                f"differs by {bad:.2f} mm2 of {area:.0f} mm2")
        ebd = _ebd(ins)
        for key, tags in inst["ebd"].items():
            got = list(ebd[key].tags)
            if [t.code for t in tags] != [t.code for t in got]:
                msgs.append(f"xrecord {key}: codes differ")
                continue
            for j, (c, o) in enumerate(zip(tags[4:], got[4:])):
                cv = tuple(c.value) if isinstance(c.value, tuple) else c.value
                ov = tuple(o.value) if isinstance(o.value, tuple) else o.value
                msgs += _compare(cv, ov, f"xrecord {key}[{j + 4}]", dev)
        assert set(ebd.keys()) == set(inst["ebd"]), (sorted(ebd.keys()), sorted(inst["ebd"]))
        assert dyn.read_dynamic_state(ins) == pytest.approx(inst["state"])
        hidden = sum(1 for s in ours if s["invisible"])
        lines.append(f"{'PASS' if not msgs else 'FAIL'} {inst['handle']:<4} {inst['u']:<5} {inst['state']} "
                     f"hidden {hidden:2d} | max dev {dev['mm']:.1e} mm, hatch region max diff "
                     f"{max(region):.2f} mm2")
        if msgs:
            failures.append((inst["u"], msgs[:25]))
    print("\nGROUND TRUTH (MASONRY BLOCK, the owner's source instances):")
    for ln in lines:
        print("  " + ln)
    for u, msgs in failures:
        print(f"  {u} (CAD value != ours):")
        for m in msgs:
            print(f"      {m}")
    assert not failures, failures


# ── lookup resolution ─────────────────────────────────────────────────


def _raw_rows(doc, name):
    """[{label: cell}] from the raw BLOCKLOOKUPACTION tags (row-major 302 cells, 94 column ids)."""
    labels = {int(k): v[1] for k, v in _param_labels(doc, name).items()}
    br = doc.blocks.get(name).block_record
    node = next(n for n in dyn.graph_nodes(doc, br) if n.dxftype() == "BLOCKLOOKUPACTION")
    tags = _tags(node)
    cells = [t.value for t in tags if t.code == 302]
    cols = [labels[t.value] for t in tags if t.code == 94]
    return [dict(zip(cols, cells[i:i + len(cols)])) for i in range(0, len(cells), len(cols))]


@pytest.fixture(scope="module")
def library():
    return ezdxf.readfile(mb.LIBRARY_PATH)


def test_every_lookup_row_resolves(src):
    rows = _raw_rows(src, SRC_NAME)
    assert len(rows) == 22
    doc = ezdxf.new("R2018")
    for i, row in enumerate(rows):
        ins, uname, state = mb.add_masonry_block(doc, doc.modelspace(), (i * 1000.0, 0), block=row["Block"])
        want = {"Block": row["Block"], "Visibility State": row["Visibility State"],
                "D": float(row["D"]), "W": float(row["W"])}
        assert {k: state[k] for k in want} == pytest.approx(want), row
        assert state["Height"] == pytest.approx(190.00003)        # not in the table: definition value
        assert dyn.read_dynamic_state(ins) == pytest.approx(state)
        assert any(not e.dxf.get("invisible", 0) for e in doc.blocks.get(uname)), row["Block"]


@pytest.mark.skip(reason="tests the downstream masonry_blocks wrapper's own behaviour (house colours / default entry / listing), not part of the fork")
def test_list_masonry_blocks_reads_the_library(src):
    info = mb.list_masonry_blocks()
    raw = _raw_rows(src, SRC_NAME)
    assert info["lookup"] == [r["Block"] for r in raw]
    assert info["rows"][0] == {"D": 140.0, "Visibility State": "Full Block", "W": 390.0, "Block": "150 Full"}
    assert info["states"] == ["Full Block", "Half Block", "H Block", "Section Full", "Section Capped",
                              "Section C Block", "Section Lintel"]
    p = info["parameters"]
    assert set(p) == {"Block", "Visibility State", "D", "W", "Height"}
    assert p["W"]["kind"] == "linear" and p["W"]["default"] == pytest.approx(190.0) and p["W"]["min"] == 0.0
    assert p["W"]["max"] is None and p["W"]["increment"] is None


@pytest.mark.skip(reason="tests the downstream masonry_blocks wrapper's own behaviour (house colours / default entry / listing), not part of the fork")
def test_default_is_150_full():
    doc = ezdxf.new("R2018")
    _, _, state = mb.add_masonry_block(doc, doc.modelspace(), (0, 0))
    assert state["Block"] == mb.DEFAULT_BLOCK == "150 Full"
    assert state["Visibility State"] == "Full Block" and state["W"] == pytest.approx(390.0)


def test_unknown_lookup_raises_and_writes_nothing():
    doc = ezdxf.new("R2018")
    mb.define_masonry_block(doc)
    mb.add_masonry_block(doc, doc.modelspace(), (0, 0))      # layer etc. created
    n, nb = len(doc.entitydb), len(doc.blocks)
    with pytest.raises(ValueError, match="no 'Block' lookup entry '250 Full'"):
        mb.add_masonry_block(doc, doc.modelspace(), (0, 0), block="250 Full")
    assert (len(doc.entitydb), len(doc.blocks)) == (n, nb)


def test_lookup_with_conflicting_value_raises():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    with pytest.raises(ValueError, match="conflicts"):
        mb.add_masonry_block(doc, msp, (0, 0), block="150 Full", width=290.0)
    with pytest.raises(ValueError, match="conflicts"):
        mb.add_masonry_block(doc, msp, (0, 0), block="150 Full", visibility="Half Block")
    with pytest.raises(ValueError, match="conflicts"):
        dyn.add_dynamic(doc, msp, mb.BLOCK_NAME, (0, 0), lookup="200 Section", linear={"D": 50.0},
                        library=mb.LIBRARY_PATH)
    # an explicit value EQUAL to the row's is accepted; Height is not in the table
    _, _, st = mb.add_masonry_block(doc, msp, (0, 0), block="150 Full", width=390.0, depth=140.0,
                                    visibility="Full Block", height=95.0)
    assert st["Height"] == pytest.approx(95.0) and st["Block"] == "150 Full"


def test_without_lookup_the_entry_is_reverse_looked_up():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    ins, _, st = mb.add_masonry_block(doc, msp, (0, 0), visibility="Section Lintel", depth=290.0, width=190.0)
    assert st["Block"] == "200 290D Lintel"
    assert dyn.read_dynamic_state(ins)["Block"] == "200 290D Lintel"
    # no row has W 240: the table's no-match label (group 305)
    _, _, st = mb.add_masonry_block(doc, msp, (0, 0), visibility="Section Full", depth=0.0, width=240.0)
    assert st["Block"] == "Custom"


def test_negative_distance_raises():
    doc = ezdxf.new("R2018")
    with pytest.raises(ValueError, match=">= 0"):
        mb.add_masonry_block(doc, doc.modelspace(), (0, 0), visibility="Full Block", height=-1.0)


def test_r2018_target_enforced():
    doc = ezdxf.new("R2013")
    with pytest.raises(ValueError, match="R2018"):
        mb.add_masonry_block(doc, doc.modelspace(), (0, 0))
    with pytest.raises(ValueError, match="R2018"):
        mb.define_masonry_block(doc)


# ── the library block: name, colours, boundaries ──────────────────────


def test_library_name_and_true_name(library):
    names = [b.name for b in library.blocks if not b.name.startswith("*")]
    assert mb.BLOCK_NAME in names and SRC_NAME not in names
    br = library.blocks.get(mb.BLOCK_NAME).block_record
    assert [t.value for t in br.get_xdata("AcDbDynamicBlockTrueName") if t.code == 1000] == [mb.BLOCK_NAME]
    assert [t.value for t in br.get_xdata("AcDbDynamicBlockGUID") if t.code == 1000] == \
        ["{B3CC9054-F5BD-8B4D-8BE9-F51DFCDCEEF7}"]
    assert len(library.modelspace()) == 0
    assert not [b.name for b in library.blocks if b.name.startswith("*U")]


def test_definition_matches_source_except_hatch_colour(src, library):
    """Same entities, layers, geometry; only the hatches' true colour added."""
    s_ents, s_sig = _signatures(src.blocks.get(SRC_NAME))
    l_ents, l_sig = _signatures(library.blocks.get(mb.BLOCK_NAME))
    assert len(s_sig) == len(l_sig) == 35
    dev = {"mm": 0.0}
    assert not _compare(s_sig, l_sig, "definition", dev)
    for s, l in zip(s_ents, l_ents):
        assert s.dxf.get("color") == l.dxf.get("color")
        if l.dxftype() == "HATCH":
            assert l.rgb == (200, 200, 200) and l.dxf.color == 8
        else:
            assert l.dxf.get("true_color") == s.dxf.get("true_color")


@pytest.mark.skip(reason="tests the downstream masonry_blocks wrapper's own behaviour (house colours / default entry / listing), not part of the fork")
def test_house_colours_on_insert():
    doc = ezdxf.new("R2018")
    ins, uname, _ = mb.add_masonry_block(doc, doc.modelspace(), (0, 0))
    assert ins.dxf.layer == "CONCRETE" and doc.layers.get("CONCRETE").dxf.color == 3     # house green
    for e in doc.blocks.get(uname):
        if e.dxftype() == "HATCH":
            assert e.rgb == (200, 200, 200)
        elif e.dxf.layer == "0":
            # outlines BYBLOCK (0) / BYLAYER (256) on layer 0 take the INSERT's layer
            # colour; the C-block hidden lines keep their ACI 9 + RGB 200,200,200
            assert e.dxf.get("color", 256) in (0, 256) or e.rgb == (200, 200, 200), e.dxf.handle
    assert "DASHED" in doc.linetypes        # the hidden lines' linetype came with the block


def _stretch_selections(doc, name):
    """{stretch action id: sorted entity INDICES in its stretch (331) list}."""
    blk = list(doc.blocks.get(name))
    index_of = {e.dxf.handle: i for i, e in enumerate(blk)}
    g = dyn.read_graph(doc, name)
    return {aid: sorted(index_of[h] for h, _ in a["select"] if h in index_of)
            for aid, a in g["actions"].items() if a["kind"] == "stretch"}


def test_viewports_boundaries_kept(src, library):
    """the owner 2026-10-02: the VIEWPORTS polylines are the hatch BOUNDARIES - kept on
    VIEWPORTS (non-plotting), associated with their hatches, in the stretches."""
    blk = list(library.blocks.get(mb.BLOCK_NAME))
    vp = [e for e in blk if e.dxf.layer == "VIEWPORTS"]
    assert len(vp) == 6 and all(e.dxftype() == "LWPOLYLINE" for e in vp)
    assert library.layers.get("VIEWPORTS").dxf.plot == 0
    sources = {s for h in blk if h.dxftype() == "HATCH" for p in h.paths for s in p.source_boundary_objects}
    by_handle = {e.dxf.handle: e for e in blk}
    for e in vp:
        assert e.dxf.handle in sources
        (hatch,) = e.get_reactors()
        assert by_handle[hatch].dxftype() == "HATCH"
    sel = _stretch_selections(library, mb.BLOCK_NAME)
    assert sel == _stretch_selections(src, SRC_NAME)
    vp_idx = {blk.index(e) for e in vp}
    in_stretch = set().union(*sel.values()) & vp_idx
    assert in_stretch == vp_idx                 # every boundary is stretched by some action


def _poly_loop(pl):
    from ezdxf import path as ezpath

    return np.array([(q.x, q.y) for q in ezpath.make_path(pl).flattening(0.005)])


def _region_vs_loops(h, loops):
    return _region_diff(_loops(h), "nonzero", loops, "nonzero")


@pytest.mark.parametrize("kw", [
    {"block": "200 Full"}, {"block": "150 Half"}, {"block": "300 H"}, {"block": "200 290D Lintel"},
    {"block": "400 Section", "height": 95.0}, {"visibility": "Half Block", "width": 290.0, "height": 290.0},
])
def test_stretched_boundary_and_hatch_move_together(kw):
    """Every associative hatch of a stretched instance fills exactly the region
    of its (stretched) boundary objects - VIEWPORTS boundaries included."""
    doc = ezdxf.new("R2018")
    _, uname, _ = mb.add_masonry_block(doc, doc.modelspace(), (0, 0), **kw)
    u = list(doc.blocks.get(uname))
    definition = list(doc.blocks.get(mb.BLOCK_NAME))
    by_handle = {e.dxf.handle: e for e in u}
    moved = 0
    for i, h in enumerate(u):
        if h.dxftype() != "HATCH":
            continue
        srcs = [by_handle[s] for p in h.paths for s in p.source_boundary_objects]
        assert srcs and all(s.dxftype() == "LWPOLYLINE" for s in srcs)
        bad, area = _region_vs_loops(h, [_poly_loop(s) for s in srcs])
        assert bad <= REGION_TOL, (kw, i, bad, area)
        for s in srcs:
            before = definition[u.index(s)]
            if not np.allclose(np.array(list(s.get_points("xy"))), np.array(list(before.get_points("xy")))):
                moved += 1
    assert moved, f"{kw}: no hatch boundary was stretched - the check proves nothing"


# ── demo output for the BricsCAD / AutoCAD check on the owner's machine ─────


@pytest.mark.skip(reason="tests the downstream masonry_blocks wrapper's own behaviour (house colours / default entry / listing), not part of the fork")
def test_demo_every_entry_audit_clean_and_written(out_dir):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    want = {}
    for i, name in enumerate(mb.list_masonry_blocks()["lookup"]):
        x, y = (i % 6) * 700.0, -(i // 6) * 600.0
        ins, _, st = mb.add_masonry_block(doc, msp, (x, y), block=name)
        want[ins.dxf.handle] = st
        msp.add_text(name, height=25.0, dxfattribs={"insert": (x - 200.0, y - 80.0)})
    ins, _, st = mb.add_masonry_block(doc, msp, (0, 900.0), block="200 Section", height=95.0, rotation=90.0)
    want[ins.dxf.handle] = st
    a = doc.audit()
    assert len(a.fixes) == 0 and len(a.errors) == 0, [f.message for f in a.fixes + a.errors]
    p = out_dir / "masonry_blocks_demo.dxf"
    doc.saveas(p)
    print(f"\nMASONRY BLOCK DEMO (for the BricsCAD check): {p}")
    r = ezdxf.readfile(p)
    ra = r.audit()
    assert len(ra.fixes) == 0 and len(ra.errors) == 0, [f.message for f in ra.fixes + ra.errors]
    inserts = list(r.modelspace().query("INSERT"))
    assert len(inserts) == 23
    for e in inserts:
        assert dyn.read_dynamic_state(e) == pytest.approx(want[e.dxf.handle])
        btag = r.blocks.get(e.dxf.name).block_record.get_xdata("AcDbBlockRepBTag")
        assert r.entitydb[next(t.value for t in btag if t.code == 1005)].dxf.name == mb.BLOCK_NAME
