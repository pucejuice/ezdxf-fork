# License: MIT
# WP6 (experimental): annotative TEXT and viewport annotation scale.  Where a
# CAD-made reference exists in dynblock_data, the expected values are READ from
# it, not restated: the AcadAnnotative XDATA (AutoCAD / BricsCAD), the dictionary
# flags of the context-data chain, the common tags of a context-data object
# (BricsCAD MTEXT-attribute variant), the SCALE record and ACAD_SCALELIST keys.
from pathlib import Path

import pytest

import ezdxf
from ezdxf.addons import annoscale as an

DATA = Path(__file__).parent / "dynblock_data"
ACAD = DATA / "autocad_saved_dynamic_instances.dxf"
BRICSCAD = DATA / "annotation_blocks_dyn.dxf"


def _need(path: Path) -> None:
    """The CAD-made fixture data is not part of the public repository; see
    dynblock_data/README.md."""
    if not path.exists():
        pytest.skip(f"CAD fixture data not in this checkout: {path.name} "
                    "(see tests/test_08_addons/dynblock_data/README.md)")


def _tags(o):
    return [(t.code, t.value) for s in o.xtags.subclasses[1:] for t in s]


@pytest.fixture(scope="module")
def acad():
    _need(ACAD)
    return ezdxf.readfile(ACAD)


@pytest.fixture(scope="module")
def bricscad():
    _need(BRICSCAD)
    return ezdxf.readfile(BRICSCAD)


@pytest.fixture
def doc():
    return ezdxf.new("R2018")


def _text(doc, **kw):
    return doc.modelspace().add_text("NOTE", height=2.5, dxfattribs={"insert": (10, 20), **kw})


# ── checked against CAD-made objects ──────────────────────────────────


def test_xdata_is_the_form_cad_writes_for_an_annotative_object(acad, doc):
    style = acad.styles.get("STUDIO_ANNO")
    cad = [(t.code, t.value) for t in style.get_xdata("AcadAnnotative")]
    t = an.make_annotative(_text(doc), ["1:20"])
    assert [(x.code, x.value) for x in t.get_xdata("AcadAnnotative")] == cad


def test_dictionary_chain_and_flags_as_cad_writes_them(bricscad, doc):
    ctx = next(o for o in bricscad.objects if "CONTEXTDATA" in o.dxftype())
    cad_scales = bricscad.entitydb[ctx.dxf.owner]
    cad_cdm = bricscad.entitydb[cad_scales.dxf.owner]
    cad_key = next(k for k, v in cad_scales.items() if v is ctx)
    t = an.make_annotative(_text(doc), ["1:20", "1:50"])
    cdm = t.get_extension_dict()[an.CDM_KEY]
    scales = cdm[an.ANNOSCALES_KEY]
    assert list(cdm.keys()) == list(cad_cdm.keys()) == [an.ANNOSCALES_KEY]
    assert scales.dxf.get("hard_owned", 0) == cad_scales.dxf.get("hard_owned", 0) == 0
    assert cdm.dxf.get("hard_owned", 0) == cad_cdm.dxf.get("hard_owned", 0) == 0
    assert cad_key.startswith("*A") and list(scales.keys()) == ["*A1", "*A2"]


def test_context_object_common_tags_match_cad(bricscad, doc):
    cad = _tags(next(o for o in bricscad.objects if "CONTEXTDATA" in o.dxftype()))
    t = an.make_annotative(_text(doc, rotation=30.0), ["1:20"])
    ctx = t.get_extension_dict()[an.CDM_KEY][an.ANNOSCALES_KEY]["*A1"]
    ours = _tags(ctx)
    # same subclass / code sequence as CAD's object up to the alignment point
    n = [c for c, _ in cad].index(11) + 1
    assert [c for c, _ in ours] == [c for c, _ in cad[:n]]
    assert ours[0:3] == cad[0:3]                         # AcDbObjectContextData 70 4, 290 default
    d = dict(ours[3:])
    assert doc.entitydb[d[340]].dxftype() == "SCALE"
    assert d[50] == pytest.approx(30.0) and tuple(d[10])[:2] == (10.0, 20.0)
    assert ctx.dxf.owner == t.get_extension_dict()[an.CDM_KEY][an.ANNOSCALES_KEY].dxf.handle


def test_scale_record_layout_matches_cad(acad, doc):
    cad = acad.rootdict["ACAD_SCALELIST"]["A0"]       # '1:1'
    h = an.ensure_annotation_scale(doc, "1:1")
    assert _tags(doc.entitydb[h]) == _tags(cad)
    assert list(doc.rootdict["ACAD_SCALELIST"].keys()) == ["A0"]


def test_existing_cad_scale_is_found_by_name_not_key(acad):
    """CAD keys ACAD_SCALELIST A0, A1, ...; the reference looked scales up by key
    and so added a second '1:20' to a CAD-made drawing."""
    doc = ezdxf.readfile(ACAD)
    n = len(doc.rootdict["ACAD_SCALELIST"])
    h = an.ensure_annotation_scale(doc, "1:20")
    assert h == acad.rootdict["ACAD_SCALELIST"]["A7"].dxf.handle
    assert len(doc.rootdict["ACAD_SCALELIST"]) == n


# ── API ───────────────────────────────────────────────────────────────


def test_one_default_and_idempotent(doc):
    t = _text(doc)
    an.make_annotative(t, ["1:20", "1:50", "1:20"], default_scale="1:50")
    n = len(doc.entitydb)
    an.make_annotative(t, ["1:20", "1:50"], default_scale="1:50")
    assert len(doc.entitydb) == n
    scales = t.get_extension_dict()[an.CDM_KEY][an.ANNOSCALES_KEY]
    defaults = [dict(_tags(o))[290] for _, o in scales.items()]
    assert defaults == [0, 1]


def test_bad_input_raises(doc):
    with pytest.raises(ValueError):
        an.make_annotative(_text(doc), [])
    with pytest.raises(ValueError, match="malformed"):
        an.make_annotative(_text(doc), ["1-20"])
    with pytest.raises(ValueError, match="default_scale"):
        an.make_annotative(_text(doc), ["1:20"], default_scale="1:50")
    with pytest.raises(NotImplementedError, match="MTEXT"):
        an.make_annotative(doc.modelspace().add_mtext("X"), ["1:20"])


def test_annotative_text_audit_clean_and_round_trip(doc, tmp_path):
    t = an.make_annotative(_text(doc), ["1:20", "1:50"])
    assert len(doc.audit().fixes) == 0
    p = tmp_path / "anno.dxf"
    doc.saveas(p)
    r = ezdxf.readfile(p)
    assert len(r.audit().fixes) == 0
    rt = r.entitydb[t.dxf.handle]
    scales = rt.get_extension_dict()[an.CDM_KEY][an.ANNOSCALES_KEY]
    assert sorted(scales.keys()) == ["*A1", "*A2"]
    assert "ACDB_TEXTOBJECTCONTEXTDATA_CLASS" in [c.dxf.name for c in r.classes]


def test_deleting_annotative_text_leaves_no_orphans(doc):
    t = an.make_annotative(_text(doc), ["1:20", "1:50"])
    doc.modelspace().delete_entity(t)
    db = doc.entitydb
    orphans = [o for o in db.values() if o.is_alive and o.dxf.get("owner", "0") not in ("0", None)
               and o.dxf.owner not in db]
    assert orphans == []


@pytest.mark.parametrize("vh,h,name", [
    (2000.0, 100.0, "1:20"), (100.0, 100.0, "1:1"), (50.0, 100.0, "2:1"),
    (250.0, 100.0, "1:2.5"), (10.0, 100.0, "10:1"),
])
def test_annotation_scale_name(vh, h, name):
    assert an.annotation_scale_name(vh, h) == name


def test_annotation_scale_name_rejects_degenerate():
    with pytest.raises(ValueError):
        an.annotation_scale_name(0.0, 100.0)


def test_set_viewport_annotation_scale(doc):
    psp = doc.paperspace()
    vp = psp.add_viewport(center=(100, 100), size=(100, 50), view_center_point=(0, 0), view_height=1000)
    assert an.set_viewport_annotation_scale(vp) == "1:20"
    xrec = vp.get_extension_dict()[an.VIEWPORT_ANNO_SCALE_XREC]
    tags = [(t.code, t.value) for t in xrec.tags]
    assert tags[0] == (90, 1) and tags[1][0] == 340
    assert an.find_annotation_scale(doc, "1:20") == tags[1][1]
    n = len(doc.entitydb)
    vp.dxf.view_height = 2500
    assert an.set_viewport_annotation_scale(vp) == "1:50"
    assert len(doc.entitydb) == n + 1           # one new SCALE, no second XRECORD
    assert len(doc.audit().fixes) == 0
