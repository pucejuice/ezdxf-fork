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


@pytest.fixture(scope="module")
def library():
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
