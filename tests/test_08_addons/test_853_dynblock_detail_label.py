"""DETAIL LABEL with its dynamic "Line Length" (LIB-DRAFTING-DETAIL-MARKER-DYNAMIC-1).

Ground truth: the three AutoCAD-placed DETAIL LABEL instances in the owner's
structural template STR_TEMPLATE_AUG_2026.dwt (2026-09-25 save, converted
2026-09-29), recorded raw by
tests/test_08_addons/dynblock_data/build_detail_label_truth.py.  All three share
``*U15``: "Line Length" 41.5 -> 49.0 mm, i.e. the underline runs 7.5 mm further
right and the hexagon (outline, WIPEOUT, LABEL, REF_PAGE) moves with it.
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


_require_fixture_data('annotation_blocks_dyn.dxf', 'detail_label_truth.json', 'section_marker_truth.json', 'detail_label_autocad_list.txt')


@pytest.fixture(autouse=True)
def _default_library(monkeypatch):
    """ezdxf ships no default library: the reference tests ran against the
    annotation library, so it is made the module default here."""
    monkeypatch.setattr(dyn, "LIBRARY_PATH", LIB)

from test_851_dynblock_section_marker import (
    _attrib_signature, _compare, _compare_xrecords, _load_raw, _u_signatures,
    _u_signatures_cad)

TRUTH = DATA / "detail_label_truth.json"
NAME = "DETAIL LABEL"


@pytest.fixture(scope="module")
def truth():
    return json.loads(TRUTH.read_text(encoding="utf-8"))


def _line_length(ebd: dict) -> float:
    """|end - base| of the one linear parameter (node 1), read straight from CAD's XRECORD."""
    pts = [Vec3(v) for c, v in ebd["1"] if c == 10]
    return (pts[1] - pts[0]).magnitude


def test_ground_truth_detail_label_instances(truth):
    assert len(truth["instances"]) == 3
    scratch = ezdxf.new("R2018")
    failures = []
    for inst in truth["instances"]:
        length = _line_length(inst["ebd"])
        assert length == pytest.approx(49.0, abs=1e-9)
        doc = ezdxf.new("R2018")
        dyn.define_dynamic_blocks(doc, [NAME])
        attribs = {next(v for c, v in a if c == 2): next(v for c, v in a if c == 1) for a in inst["attribs"]}
        ins, uname, state = dyn.add_dynamic(doc, doc.modelspace(), NAME, inst["insert"],
                                            linear={"Line Length": length}, attribs=attribs)
        assert state == {"Line Length": pytest.approx(49.0)}
        dev = {"mm": 0.0, "deg": 0.0}
        cad = _u_signatures_cad(scratch, truth["u_blocks"][inst["u"]])
        ours = _u_signatures(doc.blocks.get(uname))
        msgs = [] if len(cad) == len(ours) else [f"{len(ours)} entities, CAD has {len(cad)}"]
        for i, (c, o) in enumerate(zip(cad, ours)):
            msgs += _compare(c, o, f"entity {i} {c['type']}", dev)
        ebd = ins.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
        msgs += _compare_xrecords(inst["ebd"], dict(ebd.items()), dev)[0]
        cad_att = _attrib_signature(Vec3(inst["insert"]), [_load_raw(scratch, a) for a in inst["attribs"]])
        msgs += _compare(cad_att, _attrib_signature(Vec3(inst["insert"]), ins.attribs), "attribs", dev)
        print(f"\n{inst['handle']} {inst['u']} Line Length {length}: max dev {dev['mm']:.2e} mm")
        if msgs:
            failures.append((inst["handle"], msgs[:20]))
    assert not failures, failures


# ── fitted insert: annotation_blocks.insert_detail_label_fitted / annotations.detail_view_title ──


def _text_w(doc, att) -> float:
    from ezdxf.entities import Text
    from ezdxf.tools.text_size import text_size

    return text_size(Text.new(dxfattribs={"style": att.dxf.style, "height": att.dxf.height,
                                          "width": att.dxf.get("width", 1.0), "text": att.dxf.text},
                              doc=doc)).width


def _hexagon_left(doc, ins) -> float:
    """World x of the hexagon's left vertex in the INSERT's *U block."""
    poly = next(e for e in doc.blocks.get(ins.dxf.name) if e.dxftype() == "LWPOLYLINE")
    m = ins.matrix44()
    return min(m.transform(Vec3(x, y, 0)).x for x, y in poly.get_points("xy"))


@pytest.mark.parametrize("title", ["A", "POST FOOTING", "STRAIN POST FOOTING AND STAY BLOCK"])
@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_fitted_length_clears_the_title(title):
    """The smallest on-grid Line Length (41.5 + n x 2.5) whose hexagon clears the
    longer of the title / SCALE text by >= 2 mm; a longer one would waste < 2.5 mm."""
    from studio.drafting import annotation_blocks as ab

    doc = ezdxf.new("R2018")
    lay = doc.modelspace()
    ins, fit = ab.insert_detail_label_fitted(lay, (100.0, 50.0), detail=title,
                                             scale_text="SCALE 1:10 @ A3", label="B", ref_page="S19")
    assert dyn.read_dynamic_state(ins) == {"Line Length": pytest.approx(fit.line_length)}
    assert (fit.line_length - 41.5) / 2.5 == pytest.approx(round((fit.line_length - 41.5) / 2.5))
    text_right = max(a.dxf.insert.x + _text_w(doc, a) for a in ins.attribs if a.dxf.tag in ("DETAIL", "SCALE"))
    gap = _hexagon_left(doc, ins) - text_right
    assert gap >= 2.0 - 1e-9
    assert gap < 2.0 + 2.5 or fit.line_length == 41.5
    assert fit.right_mm == pytest.approx(-20.0 + fit.line_length)
    # the title stays where the template puts it (left of the underline), only the hexagon moves
    det = next(a for a in ins.attribs if a.dxf.tag == "DETAIL")
    assert det.dxf.insert.x == pytest.approx(100.0 - 20.12960600757775)
    lab = next(a for a in ins.attribs if a.dxf.tag == "LABEL")
    assert lab.dxf.align_point.x == pytest.approx(100.0 + fit.right_mm - 9.0)


@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_short_title_keeps_the_definition_length():
    from studio.drafting import annotation_blocks as ab

    doc = ezdxf.new("R2018")
    _, fit = ab.insert_detail_label_fitted(doc.modelspace(), (0, 0), detail="A", scale_text="1:5")
    assert fit.line_length == pytest.approx(41.5)


@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_fitted_label_at_plot_scale_in_model_space():
    """Model space at 1:20: the INSERT is scaled x20, the Line Length stays in paper mm."""
    from studio.drafting import annotations as ann

    doc = ezdxf.new("R2018")
    ins = ann.detail_view_title(doc.modelspace(), "STRAIN POST FOOTING AND STAY BLOCK", (0.0, 0.0),
                                label="C", ref_page="S20", scale_text="SCALE 1:20 @ A3", plot_scale=20.0)
    assert (ins.dxf.xscale, ins.dxf.yscale, ins.dxf.zscale) == (20.0, 20.0, 20.0)
    length = dyn.read_dynamic_state(ins)["Line Length"]
    assert length > 41.5
    det = next(a for a in ins.attribs if a.dxf.tag == "DETAIL")
    assert det.dxf.height == pytest.approx(80.0)                 # 4.0 mm on paper x 20
    assert _hexagon_left(doc, ins) - (det.dxf.insert.x + _text_w(doc, det)) >= 20.0 * 2.0 - 1e-6


@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_explicit_length_is_validated():
    from studio.drafting import annotation_blocks as ab

    doc = ezdxf.new("R2018")
    with pytest.raises(ValueError, match="increment"):
        ab.insert_detail_label_fitted(doc.modelspace(), (0, 0), detail="X", line_length=45.0)


@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_static_detail_label_already_in_the_drawing_is_refused():
    """A DETAIL LABEL brought in by define_annotation_blocks (ezdxf Importer) has
    lost its dynamic graph; the fitted insert refuses rather than writing a
    static label with a hand-drawn underline."""
    from studio.drafting import annotation_blocks as ab

    doc = ezdxf.new("R2018")
    ab.define_annotation_blocks(doc, [ab.BLOCK_DETAIL_LABEL])
    with pytest.raises(ValueError, match="static"):
        ab.insert_detail_label_fitted(doc.modelspace(), (0, 0), detail="X")


@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_below_r2018_is_refused():
    from studio.drafting import annotation_blocks as ab

    doc = ezdxf.new("R2013")
    with pytest.raises(ValueError, match="R2018"):
        ab.insert_detail_label_fitted(doc.modelspace(), (0, 0), detail="X")


@pytest.mark.skip(reason="uses the downstream annotation_blocks / annotations wrappers, which are not part of the fork")
def test_fitted_labels_audit_clean_and_render(tmp_path):
    """Three fitted labels on an A3 paper layout: ezdxf audit clean, rendered to PNG."""
    from studio.drafting import annotation_blocks as ab
    from studio.drafting import drafting_debug_dir

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    psp = doc.layout("Layout1")
    for i, t in enumerate(["A", "POST FOOTING", "STRAIN POST FOOTING AND STAY BLOCK"]):
        ab.insert_detail_label_fitted(psp, (40.0, 250.0 - 30.0 * i), detail=t,
                                      scale_text=f"SCALE 1:{10 * (i + 1)} @ A3", label="ABC"[i], ref_page="S19")
    a = doc.audit()
    assert not a.fixes and not a.errors, [f.message for f in a.fixes + a.errors]
    out = drafting_debug_dir("detail_label_dynamic")
    out.mkdir(parents=True, exist_ok=True)
    dxf = out / "detail_label_fitted.dxf"
    doc.saveas(dxf)
    r = ezdxf.readfile(dxf)
    assert len(r.layout("Layout1").query("INSERT")) == 3
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    fig = plt.figure(figsize=(8, 5))
    ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(r), MatplotlibBackend(ax)).draw_layout(r.layout("Layout1"))
    png = out / "detail_label_fitted.png"
    fig.savefig(png, dpi=150)
    plt.close(fig)
    assert png.stat().st_size > 0
    print(f"\nFITTED DETAIL LABEL render: {png}")


# ── AutoCAD reads it as the dynamic DETAIL LABEL ──────────────────────
# AutoCAD LT's console has no getpropertyvalue / setpropertyvalue /
# dumpallproperties ("bad function", measured 2026-09-29), so it can neither
# read nor SET a dynamic property through LISP.  Its LIST command does print a
# dynamic reference's effective "Block Name", the "*U" "Anonymous Name" and
# every dynamic property; RESETBLOCK makes AutoCAD re-evaluate the block's own
# graph back to the definition value.

AUTOCAD_LIST = DATA / "detail_label_autocad_list.txt"
_LIST_SCRIPT = r'''FILEDIA 0
(setvar "FILEDIA" 0)
(princ "\n>>LIST_ALL")
(command "._LIST" (ssget "_X" '((0 . "INSERT"))) "")
(setq e (ssname (ssget "_X" '((0 . "INSERT"))) 0))
(command "._RESETBLOCK" e "")
(princ "\n>>LIST_AFTER_RESET")
(command "._LIST" (ssget "_X" '((0 . "INSERT"))) "")
(princ "\n>>END\n")
'''


def _parse_list(stdout: str) -> dict:
    """``{"before" / "after": [(block name, anonymous name or None, Line Length)]}``."""
    import re

    out = {}
    for key, start, stop in (("before", ">>LIST_ALL", ">>LIST_AFTER_RESET"), ("after", ">>LIST_AFTER_RESET", None)):
        i = stdout.rfind(start)
        seg = stdout[i:stdout.rfind(stop)] if stop else stdout[i:]
        refs = []
        for chunk in seg.split("BLOCK REFERENCE")[1:]:
            name = re.search(r'Block Name: "([^"]+)"', chunk).group(1)
            anon = re.search(r'Anonymous Name: "([^"]+)"', chunk)
            length = re.search(r"Line Length:\s+([0-9.]+)", chunk)
            refs.append((name, anon.group(1) if anon else None, float(length.group(1)) if length else None))
        out[key] = refs
    return out


def _check_autocad_list(run) -> None:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    dyn.define_dynamic_blocks(doc, [NAME])
    for i, length in enumerate((61.5, 99.0)):
        dyn.add_dynamic(doc, doc.modelspace(), NAME, (0, -30 * i), linear={"Line Length": length},
                        attribs={"DETAIL": f"LEN {length}", "SCALE": "SCALE 1:10", "LABEL": "B",
                                 "REF_PAGE": "S17", "PAIR": ""})
    got = _parse_list(run(doc))
    assert sorted(got["before"]) == [(NAME, "*U1", 61.5), (NAME, "*U2", 99.0)]
    # RESETBLOCK: AutoCAD evaluates the block itself -> back to the 41.5 definition, not a *U
    assert sorted(got["after"], key=lambda r: r[2]) == [(NAME, None, 41.5), (NAME, "*U1", 61.5)]


@pytest.mark.skip(reason="needs AutoCAD Core Console (accoreconsole.exe) for a live run; not available here - run on a machine with AutoCAD")
def test_autocad_lists_the_dynamic_line_length(tmp_path):
    from studio.drafting import accore

    def run(doc):
        p = tmp_path / "detail_label_list.dxf"
        doc.saveas(p)
        stdout, timed_out = accore._run_accore(p, _LIST_SCRIPT, timeout=300)
        assert not timed_out
        return stdout

    _check_autocad_list(run)


def test_autocad_lists_the_dynamic_line_length_fake_accore():
    """Gate twin: the recorded AutoCAD LT 2027 output of the same script replayed."""
    _check_autocad_list(lambda doc: AUTOCAD_LIST.read_text(encoding="utf-8"))


def test_wipeout_partial_stretch_still_refused():
    """Only a stretch that carries EVERY boundary vertex of a WIPEOUT has a CAD
    reference (the template's *U15); moving some of them raises, nothing written."""
    doc = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(doc, [NAME])
    g = dyn.read_graph(doc, NAME)
    wipe = next(e for e in doc.blocks.get(NAME) if e.dxftype() == "WIPEOUT")
    for sel in g["actions"][9]["select"]:
        if sel[0] == wipe.dxf.handle:
            sel[1] = sel[1][:3]
    ents = {e.dxf.handle: dyn._virtual_copy(e) for e in doc.blocks.get(NAME)}
    with pytest.raises(NotImplementedError, match="WIPEOUT"):
        dyn._evaluate(g, {1: 49.0}, ents)
