# License: MIT
# WP5: deleting dynamic blocks and their *U representations must not leave
# dangling or orphaned objects.  Fixture: AutoCAD-saved DXF with 8 SECTION MARKER
# instances in Layout1 (*U3 ... *U18).
import io
from pathlib import Path

import pytest

import ezdxf
from ezdxf.lldxf import const
from ezdxf.lldxf.tagwriter import TagWriter
from ezdxf.lldxf.types import POINTER_CODES

FIXTURE = Path(__file__).parent / "dynblock_data" / "autocad_saved_dynamic_instances.dxf"
PARENT = "SECTION MARKER"


@pytest.fixture(scope="module")
def baseline():
    return _dangling(ezdxf.readfile(FIXTURE))


@pytest.fixture
def doc():
    return ezdxf.readfile(FIXTURE)


def _pairs(entity, dxfversion):
    s = io.StringIO()
    entity.export_dxf(TagWriter(s, dxfversion=dxfversion))
    lines = s.getvalue().split("\n")
    return [(int(lines[i]), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]


def _dangling(doc) -> set:
    """(dxftype, group code, handle) of every pointer / owner tag of a live
    entity whose target does not exist."""
    db = doc.entitydb
    out = set()
    for e in list(db.values()):
        if not e.is_alive:
            continue
        for code, value in _pairs(e, doc.dxfversion):
            if code in POINTER_CODES and value not in ("0", "") and value not in db:
                out.add((e.dxftype(), code, value))
    return out


def _orphans(doc) -> list:
    """Live entities whose owner (group 330) does not exist."""
    db = doc.entitydb
    return [
        (e.dxftype(), e.dxf.handle, e.dxf.owner)
        for e in db.values()
        if e.is_alive and e.dxf.get("owner", "0") not in ("0", None) and e.dxf.owner not in db
    ]


def _representations(doc, parent_name):
    parent = doc.blocks.get(parent_name).block_record.dxf.handle
    out = []
    for b in doc.blocks:
        br = b.block_record
        if br.has_xdata("AcDbBlockRepBTag"):
            tags = br.get_xdata("AcDbBlockRepBTag")
            if any(t.code == 1005 and t.value == parent for t in tags):
                out.append(b.name)
    return out


def test_fixture_has_eight_representations(doc, baseline):
    assert len(_representations(doc, PARENT)) == 8
    # ezdxf does not keep AutoCAD's (partly stale) BLKREFS list: nothing dangles
    assert baseline == set()
    assert _orphans(doc) == []


class TestDeleteRepresentationBlock:
    def test_no_orphans_after_deleting_a_u_block(self, doc, baseline):
        """BDM_DATABASE (#13D) and ACDB_ANNOTATIONSCALES (#153) own their entries
        by group 350 without the hard-owner flag (280): 1.4.3 left the
        BdmDbPersIdManager and the nested scale dictionary behind, owner gone."""
        layout = doc.layouts.get("Layout1")
        for e in layout.query('INSERT[name=="*U3"]'):
            layout.delete_entity(e)
        doc.blocks.delete_block("*U3", safe=False)
        assert _orphans(doc) == []
        assert _dangling(doc) <= baseline

    def test_audit_has_nothing_left_to_fix(self, doc):
        layout = doc.layouts.get("Layout1")
        for e in layout.query('INSERT[name=="*U3"]'):
            layout.delete_entity(e)
        doc.blocks.delete_block("*U3", safe=False)
        auditor = doc.audit()
        assert [f.message for f in auditor.fixes] == []


class TestDeleteDynamicParent:
    def test_safe_delete_refuses_a_parent_with_representations(self, doc):
        """The safety check looked for INSERTs of the parent's own name only; the
        instances insert *U blocks, so the parent was deleted."""
        with pytest.raises(const.DXFBlockInUseError):
            doc.blocks.delete_block(PARENT, safe=True)
        assert PARENT in doc.blocks

    def test_unsafe_delete_leaves_no_dangling_handles(self, doc, baseline):
        """1.4.3 left every *U record's AcDbBlockRepBTag (1005) and every
        AcDbRepData (340) pointing at the deleted parent; audit() does not check
        either, so the file stayed broken."""
        u_names = _representations(doc, PARENT)
        doc.blocks.delete_block(PARENT, safe=False)
        assert _dangling(doc) <= baseline
        assert _orphans(doc) == []
        # the instances keep their geometry as plain anonymous blocks
        layout = doc.layouts.get("Layout1")
        inserts = list(layout.query("INSERT"))
        assert sorted(e.dxf.name for e in inserts) == sorted(u_names)
        for e in inserts:
            assert len(doc.blocks.get(e.dxf.name)) == 18
            assert not doc.blocks.get(e.dxf.name).block_record.has_xdata("AcDbBlockRepBTag")
            xdict = e.get_extension_dict() if e.has_extension_dict else None
            assert xdict is None or "AcDbBlockRepresentation" not in xdict

    def test_unsafe_delete_then_audit_is_clean(self, doc, tmp_path):
        doc.blocks.delete_block(PARENT, safe=False)
        auditor = doc.audit()
        assert [f.message for f in auditor.fixes] == []
        p = tmp_path / "parent_deleted.dxf"
        doc.saveas(p)
        assert len(ezdxf.readfile(p).audit().fixes) == 0


class TestAuditRepairsBrokenRepresentations:
    """A file already written with the dangling representation data (by 1.4.3
    or any other tool) is repaired by audit()."""

    def _break(self, doc):
        parent = doc.blocks.get(PARENT).block_record.dxf.handle
        missing = "FFFFFF"
        assert missing not in doc.entitydb
        for b in doc.blocks:
            br = b.block_record
            if br.has_xdata("AcDbBlockRepBTag"):
                br.set_xdata(
                    "AcDbBlockRepBTag",
                    [(t.code, missing if t.code == 1005 else t.value)
                     for t in br.get_xdata("AcDbBlockRepBTag")],
                )
        for e in doc.objects:
            if e.dxftype() == "ACDB_BLOCKREPRESENTATION_DATA":
                for sub in e.xtags.subclasses:
                    for i, t in enumerate(sub):
                        if t.code == 340 and t.value == parent:
                            sub[i] = type(t)(340, missing)

    def test_audit_removes_dangling_representation_data(self, doc, baseline):
        self._break(doc)
        assert _dangling(doc) - baseline        # broken
        auditor = doc.audit()
        assert _dangling(doc) <= baseline
        assert _orphans(doc) == []
        assert len(auditor.fixes) == 16         # 8 BTag XDATA + 8 representation dicts
        for e in doc.layouts.get("Layout1").query("INSERT"):
            assert not doc.blocks.get(e.dxf.name).block_record.has_xdata("AcDbBlockRepBTag")
