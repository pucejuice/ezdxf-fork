"""Build ``section_marker_truth.json`` - the CAD-made SECTION MARKER ground truth.

Source: the structural template ``STR_TEMPLATE_AUG_2026.dwt`` converted to DXF by
the Director (2026-09-24, ``STR_TEMPLATE_AUG_2026_template.dxf``, attached to
LIB-DYNAMIC-BLOCKS-ROTATE-FLIP-VIS-1).  Every placed SECTION MARKER instance
whose ``*U`` representation block points back at SECTION MARKER
(``AcDbBlockRepBTag``) is recorded exactly as CAD wrote it:

* the ``*U`` block's entities as raw DXF (group code, value) pairs - proxy
  graphics (160/310 in the AcDbEntity subclass) dropped, they carry no geometry;
* the INSERT's placement and its ATTRIBs (raw pairs);
* the INSERT's representation XRECORDs
  (``AcDbBlockRepresentation/AppDataCache/ACAD_ENHANCEDBLOCKDATA/<node id>``).

The template is 109 MB, so it is not committed; this script is the provenance.

Usage::

    python tests/fixtures/dynamic_blocks/build_section_marker_truth.py TEMPLATE.dxf
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "section_marker_truth.json"
NAME = "SECTION MARKER"


def _pairs(entity, dxfversion) -> list:
    from ezdxf.lldxf.tagwriter import TagWriter

    s = io.StringIO()
    entity.export_dxf(TagWriter(s, dxfversion=dxfversion))
    lines = s.getvalue().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    pairs = [[int(lines[i]), lines[i + 1].strip()] for i in range(0, len(lines), 2)]
    out, in_entity = [], False
    for code, value in pairs:
        if code == 100:
            in_entity = value == "AcDbEntity"
        if in_entity and code in (92, 160, 310):
            continue          # proxy graphics
        out.append([code, value])
    return out


def _jv(v):
    if isinstance(v, (tuple, list)):
        return [float(x) for x in v]
    return v


def build(template: Path, block: str = NAME) -> dict:
    """Every CAD-made instance of *block* in *template* (see the module docstring)."""
    import ezdxf

    doc = ezdxf.readfile(template)
    parent = doc.blocks.get(block).block_record.dxf.handle
    u_blocks, instances = {}, []
    for layout in doc.layouts:
        for e in layout.query("INSERT"):
            name = e.dxf.name
            if not name.startswith("*U") or not e.has_extension_dict:
                continue
            br = doc.blocks.get(name).block_record
            if not br.has_xdata("AcDbBlockRepBTag"):
                continue
            if next((t.value for t in br.get_xdata("AcDbBlockRepBTag") if t.code == 1005), None) != parent:
                continue
            if name not in u_blocks:
                u_blocks[name] = [_pairs(x, doc.dxfversion) for x in doc.blocks.get(name)]
            rep = e.get_extension_dict()["AcDbBlockRepresentation"]
            ebd = rep["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
            instances.append({
                "handle": e.dxf.handle, "layout": layout.name, "u": name,
                "insert": list(e.dxf.insert), "rotation": e.dxf.rotation,
                "scale": [e.dxf.xscale, e.dxf.yscale, e.dxf.zscale],
                "attribs": [_pairs(a, doc.dxfversion) for a in e.attribs],
                "ebd": {k: [[t.code, _jv(t.value)] for t in xr.tags] for k, xr in ebd.items()},
            })
    raw = template.read_bytes()
    return {
        "source": {"file": template.name, "size": len(raw), "sha1": hashlib.sha1(raw).hexdigest(),
                   "made_by": "AutoCAD (the owner's template STR_TEMPLATE_AUG_2026.dwt), converted to DXF 2026-09-24"},
        "block": block, "u_blocks": u_blocks, "instances": instances,
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    data = build(Path(argv[0]))
    OUT.write_text(json.dumps(data, indent=0), encoding="utf-8")
    print(f"{len(data['instances'])} instances, {len(data['u_blocks'])} *U blocks -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
