# License: MIT
# Tests added by the fork (not ported): the BlockLayout.dynamic API, the
# library default, and every lookup row of the masonry library.
from pathlib import Path

import pytest

import ezdxf
from ezdxf.addons import dynblock as dyn

DATA = Path(__file__).parent / "dynblock_data"
ANNOTATION_LIB = DATA / "annotation_blocks_dyn.dxf"
MASONRY_LIB = DATA / "masonry_blocks_dyn.dxf"
MASONRY = "MASONRY BLOCK"


def _need(*paths: Path) -> None:
    """The CAD-made fixture data is not part of the public repository; see
    dynblock_data/README.md."""
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        pytest.skip(f"CAD fixture data not in this checkout: {missing} "
                    "(see tests/test_08_addons/dynblock_data/README.md)")


@pytest.fixture(scope="module")
def library():
    _need(ANNOTATION_LIB)
    return ezdxf.readfile(ANNOTATION_LIB)


def test_static_block_has_no_dynamic_definition(library):
    assert library.blocks.get("NORTH POINT").dynamic is None
    assert ezdxf.new().blocks.new("STATIC").dynamic is None


def test_dynamic_definition_of_section_marker(library):
    d = library.blocks.get("SECTION MARKER").dynamic
    assert isinstance(d, dyn.DynamicBlockDefinition)
    assert d.is_supported and d.unsupported == []
    assert {p["label"]: p["kind"] for p in d.parameters.values()} == {
        "Arm Angle": "rotation", "Label Arm": "linear", "Arm Gap": "linear",
        "Tail Arm": "linear", "Tail Direction": "flip", "Visibility State": "visibility"}
    assert d.parameter("Label Arm")["kind"] == "linear"
    # evaluation order is derived from the graph: rotation before its rotate action
    assert d.order.index(56) < d.order.index(61)
    d.require_supported()


def test_unsupported_types_are_reported_and_raise(library, monkeypatch):
    real = dyn.graph_nodes

    class _Fake:
        def dxftype(self):
            return "BLOCKARRAYACTION"

    monkeypatch.setattr(dyn, "graph_nodes", lambda d, br: real(d, br) + [_Fake()])
    d = library.blocks.get("NOTES").dynamic
    assert d.unsupported == ["BLOCKARRAYACTION"] and not d.is_supported
    with pytest.raises(NotImplementedError, match="BLOCKARRAYACTION"):
        d.require_supported()


def test_no_library_raises_clearly():
    doc = ezdxf.new("R2018")
    with pytest.raises(dyn.DynamicBlockError, match="library="):
        dyn.define_dynamic_blocks(doc)


def _raw_rows(doc, name):
    """[{label: cell}] straight from the raw BLOCKLOOKUPACTION tags."""
    br = doc.blocks.get(name).block_record
    labels = {}
    for n in dyn.graph_nodes(doc, br):
        if n.dxftype().endswith("PARAMETER"):
            t = [x for sub in n.xtags.subclasses for x in sub]
            nid = next(x.value for x in t if x.code == 90)
            code = 303 if n.dxftype() == "BLOCKLOOKUPPARAMETER" else 305
            labels[nid] = next((x.value for x in t if x.code == code and x.value), None) or \
                next(x.value for x in t if x.code == 300)
    node = next(n for n in dyn.graph_nodes(doc, br) if n.dxftype() == "BLOCKLOOKUPACTION")
    tags = [x for sub in node.xtags.subclasses for x in sub]
    cells = [x.value for x in tags if x.code == 302]
    cols = [labels[x.value] for x in tags if x.code == 94]
    return [dict(zip(cols, cells[i:i + len(cols)])) for i in range(0, len(cells), len(cols))]


def test_every_lookup_row_of_the_masonry_library_resolves():
    """The library-side twin of the reference's test_every_lookup_row_resolves
    (which reads the rows from the CAD source drawing, not supplied)."""
    _need(MASONRY_LIB)
    lib = ezdxf.readfile(MASONRY_LIB)
    rows = _raw_rows(lib, MASONRY)
    assert len(rows) == 22
    doc = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(doc, [MASONRY], library=MASONRY_LIB)
    for i, row in enumerate(rows):
        ins, uname, state = dyn.add_dynamic(doc, doc.modelspace(), MASONRY, (i * 1000.0, 0),
                                            lookup=row["Block"], library=MASONRY_LIB)
        want = {"Block": row["Block"], "Visibility State": row["Visibility State"],
                "D": float(row["D"]), "W": float(row["W"])}
        assert {k: state[k] for k in want} == pytest.approx(want), row
        assert state["Height"] == pytest.approx(190.00003)
        assert dyn.read_dynamic_state(ins) == pytest.approx(state)
        assert any(not e.dxf.get("invisible", 0) for e in doc.blocks.get(uname)), row["Block"]
    a = doc.audit()
    assert len(a.fixes) == 0 and len(a.errors) == 0


# ── WP2: a block using a complex linetype can be transplanted ─────────

COMPLEX = (
    'A,.5,-.2,["AA",LT_SERIF,S=.1,U=0.0,X=-0.1,Y=-.05],-.2,'
    '["BB",LT_ISO,S=.1,U=0.0,X=-0.1,Y=-.05],-.2,'
    "[132,ltypeshp.shx,x=-.1,s=.1],-.1,1"
)


@pytest.fixture
def complex_library(tmp_path):
    """The annotation library with one NOTES line on a complex linetype that has
    two text elements (two styles) and a shape element."""
    _need(ANNOTATION_LIB)
    lib = ezdxf.readfile(ANNOTATION_LIB)
    lib.styles.add("LT_SERIF", font="romant.shx")
    lib.styles.add("LT_ISO", font="isocp.shx")
    lib.linetypes.add("TWO_TEXT_SHAPE", pattern=COMPLEX, length=2.5)
    line = next(e for e in lib.blocks.get("NOTES") if e.dxftype() == "LINE")
    line.dxf.linetype = "TWO_TEXT_SHAPE"
    p = tmp_path / "complex_lib.dxf"
    lib.saveas(p)
    return p


def _style_handles(ltype):
    return ltype.pattern_tags.get_style_handles()


def test_block_with_complex_linetype_is_transplanted(complex_library, tmp_path):
    lib = ezdxf.readfile(complex_library)
    doc = ezdxf.new("R2018")
    assert dyn.define_dynamic_blocks(doc, ["NOTES"], library=lib) == ["NOTES"]
    ltype = doc.linetypes.get("TWO_TEXT_SHAPE")
    src_tags = list(lib.linetypes.get("TWO_TEXT_SHAPE").pattern_tags.tags)
    got_tags = list(ltype.pattern_tags.tags)
    # the library's own pattern tags, only the style handles differ
    assert [t.code for t in got_tags] == [t.code for t in src_tags]
    assert [t for t in got_tags if t.code != 340] == [t for t in src_tags if t.code != 340]
    assert _style_handles(ltype) == [
        doc.styles.get("LT_SERIF").dxf.handle,
        doc.styles.get("LT_ISO").dxf.handle,
        doc.styles.find_shx("ltypeshp.shx").dxf.handle,
    ]
    assert doc.styles.get("LT_SERIF").dxf.font == "romant.shx"
    # an instance on it, audit clean, survives save / reload
    ins, uname, _ = dyn.add_stretched(doc, doc.modelspace(), "NOTES", (0, 0), 12.0, library=lib)
    a = doc.audit()
    assert len(a.fixes) == 0 and len(a.errors) == 0, [f.message for f in a.fixes + a.errors]
    p = tmp_path / "out.dxf"
    doc.saveas(p)
    r = ezdxf.readfile(p)
    rl = r.linetypes.get("TWO_TEXT_SHAPE")
    assert all(h in r.entitydb for h in _style_handles(rl))
    assert [r.entitydb[h].dxf.get("name", "") for h in _style_handles(rl)] == ["LT_SERIF", "LT_ISO", ""]


def test_existing_target_linetype_is_kept(complex_library):
    lib = ezdxf.readfile(complex_library)
    doc = ezdxf.new("R2018")
    doc.linetypes.add("TWO_TEXT_SHAPE", [1.0, 0.5, -0.5], description="target's own")
    dyn.define_dynamic_blocks(doc, ["NOTES"], library=lib)
    assert doc.linetypes.get("TWO_TEXT_SHAPE").dxf.description == "target's own"


# ── survey ────────────────────────────────────────────────────────────


def test_survey_of_a_drawing_without_dynamic_blocks():
    doc = ezdxf.new("R2018")
    doc.blocks.new("STATIC").add_line((0, 0), (1, 0))
    assert dyn.survey(doc) == []
    assert dyn.survey_summary([])["blocks"] == 0


def test_survey_summary_ranks_missing_types():
    records = [
        {"name": "A", "status": "unsupported", "unsupported": ["BLOCKXYPARAMETER"], "seed": None},
        {"name": "B", "status": "unsupported", "unsupported": ["BLOCKARRAYACTION", "BLOCKXYPARAMETER"],
         "seed": None},
        {"name": "C", "status": "supported", "unsupported": [], "seed": False},
    ]
    s = dyn.survey_summary(records)
    assert s["status"] == {"unsupported": 2, "supported": 1}
    assert s["unsupported_types"][0] == ("BLOCKXYPARAMETER", 2)
    assert s["sole_blocker"] == [("BLOCKXYPARAMETER", 1)]
    assert s["seedless_supported"] == ["C"]


def test_survey_of_the_annotation_library(library):
    records = {r["name"]: r for r in dyn.survey(library, library=library)}
    assert set(records) == {"NOTES", "SCHEDULE HEADER", "SCHEDULE ROW", "SECTION MARKER",
                            "SECTION LABEL", "DETAIL LABEL", "PLAN LABEL", "TITLE LABEL"}
    sm = records["SECTION MARKER"]
    assert sm["status"] == "supported" and sm["seed"] is True
    assert sm["parameters"] == {"BLOCKROTATIONPARAMETER": 1, "BLOCKLINEARPARAMETER": 3,
                                "BLOCKFLIPPARAMETER": 1, "BLOCKVISIBILITYPARAMETER": 1}
    assert sm["actions"] == {"BLOCKROTATEACTION": 1, "BLOCKSTRETCHACTION": 3, "BLOCKFLIPACTION": 1}
    # the same refusal add_dynamic gives (test_uncopyable_entity_raises_before_writing)
    assert records["SECTION LABEL"]["status"] == "not implemented"
    assert "CONTEXTDATA" in records["SECTION LABEL"]["reasons"][0]
    n = len(library.entitydb)
    dyn.survey(library)
    assert len(library.entitydb) == n       # read-only


def test_survey_reports_unsupported_types(library, monkeypatch):
    real = dyn.graph_nodes

    class _Fake:
        def dxftype(self):
            return "BLOCKXYPARAMETER"

    monkeypatch.setattr(dyn, "graph_nodes", lambda d, br: real(d, br) + [_Fake()] if real(d, br) else [])
    records = dyn.survey(library, ["NOTES"])
    assert records[0]["status"] == "unsupported"
    assert records[0]["unsupported"] == ["BLOCKXYPARAMETER"]
    assert records[0]["parameters"]["BLOCKXYPARAMETER"] == 1


def test_survey_flags_a_flip_of_text(library):
    """A flip selection holding TEXT is refused by add_dynamic; the survey says so."""
    doc = ezdxf.new("R2018")
    dyn.define_dynamic_blocks(doc, ["SECTION MARKER"], library=library)
    g = dyn.read_graph(doc, "SECTION MARKER")
    flip = next(a for a in g["actions"].values() if a["kind"] == "flip")
    block = doc.blocks.get("SECTION MARKER")
    text = block.add_text("X")
    problems = dyn._entity_problems(doc, block, {**g, "actions": {
        flip["id"]: {**flip, "selection": flip["selection"] + [text.dxf.handle]}}})
    assert any("TEXT" in p and "flip" in p for p in problems)


def test_survey_command_line(library, tmp_path, capsys):
    out = tmp_path / "survey.csv"
    assert dyn._main([str(ANNOTATION_LIB), "--library", str(ANNOTATION_LIB), "--csv", str(out)]) == 0
    text = capsys.readouterr().out
    assert "8 dynamic blocks" in text and "SECTION MARKER" in text
    rows = out.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("name,status,seed") and len(rows) == 9
