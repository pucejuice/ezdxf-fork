# License: MIT
# Regression tests for silent Importer and linetype defects in ezdxf 1.4.3.
import math

import pytest

import ezdxf
from ezdxf import xref
from ezdxf.addons.importer import Importer

TWO_TEXT_PATTERN = (
    'A,.5,-.2,["AA",STYLE_A,S=.1,U=0.0,X=-0.1,Y=-.05],-.2,'
    '["BB",STYLE_B,S=.1,U=0.0,X=-0.1,Y=-.05],-.25'
)


def style_handles(linetype) -> list[str]:
    return [tag.value for tag in linetype.pattern_tags.tags if tag.code == 340]


def handle_of_style(doc, name: str) -> str:
    return doc.styles.get(name).dxf.handle


def make_source():
    doc = ezdxf.new("R2018")
    doc.styles.add("STYLE_A", font="romans.shx")
    doc.styles.add("STYLE_B", font="romand.shx")
    doc.linetypes.add("TWO_TEXT", pattern=TWO_TEXT_PATTERN, length=1.5)
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"linetype": "TWO_TEXT"})
    return doc


class TestImporterComplexLinetypeStyles:
    def test_existing_target_linetype_keeps_its_text_style(self):
        """WP4.1: update_complex_linetypes() must not touch linetypes that
        were not imported."""
        source = make_source()
        target = ezdxf.new("R2018")
        target.styles.add("TARGET_STYLE", font="isocp.shx")
        target.linetypes.add(
            "TARGET_LT",
            pattern='A,.5,-.2,["T",TARGET_STYLE,S=.1,U=0.0,X=0,Y=0],-.25',
            length=1,
        )
        expected = handle_of_style(target, "TARGET_STYLE")

        importer = Importer(source, target)
        importer.import_modelspace()
        importer.finalize()

        assert style_handles(target.linetypes.get("TARGET_LT")) == [expected]

    def test_style_already_in_target_is_reused_not_replaced_by_standard(self):
        """WP4.1: an imported linetype whose text style already exists in the
        target (and is not replaced) points at that existing style."""
        source = make_source()
        target = ezdxf.new("R2018")
        target.styles.add("STYLE_A", font="romans.shx")
        target.styles.add("STYLE_B", font="romand.shx")

        importer = Importer(source, target)
        importer.import_modelspace()
        importer.finalize()

        assert style_handles(target.linetypes.get("TWO_TEXT")) == [
            handle_of_style(target, "STYLE_A"),
            handle_of_style(target, "STYLE_B"),
        ]

    def test_every_style_handle_is_remapped(self):
        """WP4.2: all group-340 tags are remapped, not only the first one."""
        source = make_source()
        target = ezdxf.new("R2018")

        importer = Importer(source, target)
        importer.import_modelspace()
        importer.finalize()

        assert style_handles(target.linetypes.get("TWO_TEXT")) == [
            handle_of_style(target, "STYLE_A"),
            handle_of_style(target, "STYLE_B"),
        ]

    def test_xref_loader_remaps_every_style_handle(self):
        """WP4.2: same defect in ezdxf.xref (register/map_resources)."""
        source = make_source()
        target = ezdxf.new("R2018")
        loader = xref.Loader(source, target)
        loader.load_linetypes(["TWO_TEXT"])
        loader.execute()

        assert style_handles(target.linetypes.get("TWO_TEXT")) == [
            handle_of_style(target, "STYLE_A"),
            handle_of_style(target, "STYLE_B"),
        ]


class TestLinetypePatternStyleHandles:
    def test_get_style_handles_returns_all(self):
        doc = make_source()
        ltype = doc.linetypes.get("TWO_TEXT")
        assert ltype.pattern_tags.get_style_handles() == [
            handle_of_style(doc, "STYLE_A"),
            handle_of_style(doc, "STYLE_B"),
        ]

    def test_map_style_handles(self):
        doc = make_source()
        ltype = doc.linetypes.get("TWO_TEXT")
        a = handle_of_style(doc, "STYLE_A")
        ltype.pattern_tags.map_style_handles(lambda h: "FF" if h == a else h)
        assert style_handles(ltype) == ["FF", handle_of_style(doc, "STYLE_B")]


class TestLinCompilerRotation:
    def element_tags(self, pattern: str) -> dict[int, float]:
        doc = ezdxf.new()
        ltype = doc.linetypes.add("X", pattern=pattern, length=1)
        tags = list(ltype.pattern_tags.tags)
        # tags of the first embedded element: from the first 74 != 0
        start = next(i for i, t in enumerate(tags) if t.code == 74 and t.value)
        result = {}
        for tag in tags[start:]:
            if tag.code in result or tag.code == 49:
                break
            result[tag.code] = tag.value
        return result

    def test_relative_rotation_is_default(self):
        tags = self.element_tags('A,.5,-.2,["T",STANDARD,S=.1,R=30,X=0,Y=0],-.2')
        assert tags[74] == 2
        assert tags[50] == pytest.approx(30)

    def test_absolute_rotation_sets_bit_1(self):
        """WP4.4 (related): 'A=' was silently dropped (mapped to group code 0).
        DXF reference: 74 bit 1 = code 50 is an absolute rotation."""
        tags = self.element_tags('A,.5,-.2,["T",STANDARD,S=.1,A=30,X=0,Y=0],-.2')
        assert tags[74] == 2 | 1
        assert tags[50] == pytest.approx(30)

    def test_unknown_parameter_raises(self):
        doc = ezdxf.new()
        with pytest.raises(ezdxf.DXFValueError):
            doc.linetypes.add(
                "X", pattern='A,.5,-.2,["T",STANDARD,Q=1],-.2', length=1
            )


class TestLayerLinetypeProperty:
    def test_layer_linetype_property_sets_dxf_attribute(self):
        """WP4.3: layer.linetype = 'X' must not bind a stray attribute."""
        doc = ezdxf.new()
        layer = doc.layers.add("L1")
        layer.linetype = "DASHED"
        assert layer.dxf.linetype == "DASHED"
        assert layer.linetype == "DASHED"
        assert "linetype" not in vars(layer)

    def test_layer_linetype_property_default(self):
        doc = ezdxf.new()
        assert doc.layers.add("L2").linetype == "Continuous"


class TestImportBlockNameCase:
    def test_mixed_case_references_import_one_block(self):
        """WP4.5: block names are case-insensitive in DXF, the Importer cache
        was not, so one source block was imported twice."""
        source = ezdxf.new("R2018")
        block = source.blocks.new("_ArchTick")
        block.add_line((0, 0), (1, 1))
        msp = source.modelspace()
        msp.add_blockref("_ArchTick", (0, 0))
        msp.add_blockref("_ARCHTICK", (1, 0))
        target = ezdxf.new("R2018")

        importer = Importer(source, target)
        importer.import_modelspace()
        importer.finalize()

        names = [b.name for b in target.blocks if b.name.upper() == "_ARCHTICK"]
        assert names == ["_ArchTick"]
        inserts = target.modelspace().query("INSERT")
        assert [e.dxf.name for e in inserts] == ["_ArchTick", "_ArchTick"]

    def test_existing_block_differing_only_in_case(self):
        """new(setup=True) defines '_ARCHTICK'; importing '_ArchTick' with
        rename=False reuses it and returns the name as stored in the target."""
        source = ezdxf.new("R2018")
        source.blocks.new("_ArchTick").add_line((0, 0), (1, 1))
        target = ezdxf.new("R2018", setup=True)
        count = len(target.blocks)

        importer = Importer(source, target)
        assert importer.import_block("_ArchTick", rename=False) == "_ARCHTICK"
        assert importer.import_block("_archtick", rename=False) == "_ARCHTICK"
        assert len(target.blocks) == count


class TestImporterMixedTextAndShapeLinetype:
    """A complex linetype with a text element AND a shape element: the second
    group-340 handle points at a nameless shape-file STYLE entry.  1.4.3 left it
    pointing at the SOURCE document's handle - AutoCAD then loads the file but
    cannot SAVEAS DXF (ErrorStatus 53, reported by the downstream code)."""

    PATTERN = (
        'A,.5,-.2,["T",STYLE_A,S=.1,U=0.0,X=-0.1,Y=-.05],-.2,'
        "[132,ltypeshp.shx,x=-.1,s=.1],-.1,1"
    )

    def test_text_and_shape_handles_are_remapped(self):
        source = ezdxf.new("R2018")
        source.styles.add("STYLE_A", font="romans.shx")
        source.linetypes.add("MIXED", pattern=self.PATTERN, length=2.0)
        source.modelspace().add_line((0, 0), (1, 0), dxfattribs={"linetype": "MIXED"})
        target = ezdxf.new("R2018")

        importer = Importer(source, target)
        importer.import_modelspace()
        importer.finalize()

        handles = style_handles(target.linetypes.get("MIXED"))
        assert handles == [
            handle_of_style(target, "STYLE_A"),
            target.styles.find_shx("ltypeshp.shx").dxf.handle,
        ]
        assert all(h in target.entitydb for h in handles)
