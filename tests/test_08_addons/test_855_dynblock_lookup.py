"""dynamic_blocks: LOOKUP parameter + LOOKUP action (LIB-DRAFTING-MASONRY-BLOCK-DYN-1).

Source: the owner's reinforced masonry dynamic block (``Reinforced Masonry Block.dxf``,
2026-10-02, copied to ``tests/test_08_addons/dynblock_data/`` - not supplied yet; the tests also run on the library built from it).  Its graph has a
BLOCKLOOKUPPARAMETER 'Block' driving a BLOCKLOOKUPACTION 'Lookup1' whose table
binds a size / type name to the visibility state 'Display' and the linear
parameters 'D' and 'W'.  The expected table is read here straight from the raw
DXF tags (group 302 cells, row-major), independently of the lib's parser.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from ezdxf.addons import dynblock as dyn

DATA = Path(__file__).parent / "dynblock_data"
SRC = DATA / "reinforced_masonry_block.dxf"
LIB = DATA / "masonry_blocks_dyn.dxf"


def _require_fixture_data(*names: str) -> None:
    """The CAD-made fixture data is not part of the public repository; see
    dynblock_data/README.md.  Without it this module is skipped."""
    missing = [n for n in names if not (DATA / n).exists()]
    if missing:
        pytest.skip(f"CAD fixture data not in this checkout: {missing} "
                    "(see tests/test_08_addons/dynblock_data/README.md)", allow_module_level=True)


_require_fixture_data('masonry_blocks_dyn.dxf')

# The source drawing names the block "Block"; the library built from it, "MASONRY
# BLOCK" (same graph).  Every test runs on the library, and on the source when
# it is present.
_SOURCES = [pytest.param((LIB, "MASONRY BLOCK"), id="library"),
            pytest.param((SRC, "Block"), id="source",
                         marks=pytest.mark.skipif(not SRC.exists(), reason="source drawing not supplied"))]


@pytest.fixture(scope="module", params=_SOURCES)
def src(request):
    path, name = request.param
    doc = ezdxf.readfile(path)
    doc.block_name = name
    return doc


def _raw_table(doc):
    """(rows, cols, cells, column param ids) from the raw BLOCKLOOKUPACTION tags."""
    br = doc.blocks.get(doc.block_name).block_record
    node = next(n for n in dyn.graph_nodes(doc, br) if n.dxftype() == "BLOCKLOOKUPACTION")
    tags = [t for sub in node.xtags.subclasses for t in sub]
    rows = next(t.value for t in tags if t.code == 92)
    cols = next(t.value for t in tags if t.code == 93)
    cells = [t.value for t in tags if t.code == 302]
    pids = [t.value for t in tags if t.code == 94]
    return rows, cols, cells, pids


def test_graph_with_lookup_is_supported(src):
    g = dyn.read_graph(src, src.block_name)
    assert g["unsupported"] == []
    kinds = sorted((p["label"], p["kind"]) for p in g["params"].values())
    assert kinds == [("Block", "lookup"), ("D", "linear"), ("Height", "linear"), ("Visibility State", "visibility"),
                     ("W", "linear")]
    look = [a for a in g["actions"].values() if a["kind"] == "lookup"]
    assert len(look) == 1
    # the lookup action and its columns are connected both ways in the graph (a
    # cycle); the lookup is resolved before evaluation, so the order exists
    assert set(g["order"]) >= {p for p in g["params"]}


def test_lookup_table_matches_raw_tags(src):
    rows, cols, cells, pids = _raw_table(src)
    assert (rows, cols) == (22, 4) and len(cells) == rows * cols
    g = dyn.read_graph(src, src.block_name)
    a = next(a for a in g["actions"].values() if a["kind"] == "lookup")
    assert [c["param_id"] for c in a["columns"]] == pids
    assert len(a["rows"]) == rows
    for i, row in enumerate(a["rows"]):
        raw = cells[i * cols:(i + 1) * cols]
        for c, got, want in zip(a["columns"], row, raw):
            kind = g["params"][c["param_id"]]["kind"]
            assert got == (float(want) if kind == "linear" else want)
    names = [r[3] for r in a["rows"]]
    assert names[0] == "150 Full" and names[-1] == "400 Section" and len(set(names)) == 22
