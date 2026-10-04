"""Build ``schedule_truth.json`` - CAD-made SCHEDULE ROW / SCHEDULE HEADER ground truth.

Source: the structural template ``STR_TEMPLATE_AUG_2026.dwt`` converted to DXF by
the Director (2026-09-24, ``STR_TEMPLATE_AUG_2026_template.dxf``, attached to
LIB-DYNAMIC-BLOCKS-ROTATE-FLIP-VIS-1).  AutoCAD placed 260 SCHEDULE ROW and 32
SCHEDULE HEADER instances in it (every ``*U`` whose ``AcDbBlockRepBTag`` points
at the block, plus the plain INSERTs of the definition), across modelspace,
the paperspace layouts and the named blocks that nest them.  For each DISTINCT
dynamic state (Row Height / Column Width / visibility) the first ``*U``
instance is recorded:

* ``state`` - the INSERT's state as :func:`ezdxf.addons.dynblock.read_dynamic_state`
  reads it from the representation XRECORDs;
* ``entities`` - the ``*U`` block's entities, in order, as plain geometry
  (type, invisible flag, points; ATTDEF tag / insert / align point; HATCH edges
  and seed points);
* ``mark_align`` - the instance's MARK ATTRIB alignment point relative to the
  INSERT point (the visible text CAD placed);
* ``description_lines`` / ``description`` - the DESCRIPTION text and its line
  count (``wrapped_line_count`` on the DESCRIPTION ATTDEF's MTEXT), for rows.

``counts`` holds, per state, how many instances CAD wrote (plain INSERTs included).

The template is 109 MB, so it is not committed; this script is the provenance.

Usage::

    python tests/fixtures/dynamic_blocks/build_schedule_truth.py TEMPLATE.dxf
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "schedule_truth.json"
NAMES = ("SCHEDULE ROW", "SCHEDULE HEADER")


def _r(v, n: int = 2) -> list:
    return [round(float(c), 9) for c in list(v)[:n]]


def entity_geometry(e) -> dict:
    """Plain, comparable geometry of one block entity (shared with the test)."""
    t = e.dxftype()
    g = {"type": t, "invisible": int(e.dxf.get("invisible", 0))}
    if t == "LINE":
        g["points"] = [_r(e.dxf.start), _r(e.dxf.end)]
    elif t == "LWPOLYLINE":
        g["points"] = [_r(p) for p in e.get_points("xy")]
    elif t in ("ATTDEF", "TEXT"):
        # the DEFINING point only: a justified text's insert (group 10) is derived
        # by CAD from its alignment point and glyph widths (*U1325 carries 3.444211
        # where the definition has 3.444133 - same alignment point, same text)
        g["tag"] = e.dxf.get("tag", "")
        justified = e.dxf.get("halign", 0) or e.dxf.get("valign", 0)
        g["points"] = [_r(e.dxf.align_point if justified else e.dxf.insert)]
    elif t == "HATCH":
        paths = []
        for p in e.paths:
            if hasattr(p, "vertices"):
                paths.append([_r(v) for v in p.vertices])
            else:
                edges = []
                for ed in p.edges:
                    if ed.type.name != "LINE":
                        raise ValueError(f"HATCH {e.dxf.handle}: {ed.type.name} edge not recorded")
                    edges.append([_r(ed.start), _r(ed.end)])
                paths.append(edges)
        g["paths"] = paths
        g["seeds"] = [_r(s) for s in e.seeds]
    else:
        raise ValueError(f"{t} {e.dxf.handle}: not recorded")
    return g


def build(template: Path) -> dict:
    import ezdxf

    from ezdxf.addons import dynblock as db

    doc = ezdxf.readfile(template)
    containers = [doc.modelspace()] + [lay for lay in doc.layouts if lay.name != "Model"]
    containers += [b for b in doc.blocks if not b.name.startswith("*")]
    desc_props = {}
    for n in NAMES:
        att = next((a for a in doc.blocks.get(n).query("ATTDEF") if a.dxf.tag == "DESCRIPTION"), None)
        if att is not None and att.has_embedded_mtext_entity:
            vm = att.virtual_mtext_entity()
            desc_props[n] = {k: vm.dxf.get(k) for k in ("char_height", "width", "style", "attachment_point")
                             if vm.dxf.hasattr(k)}
    cases: dict = {}
    counts: dict = {}
    for cont in containers:
        for ins in cont.query("INSERT"):
            blk = doc.blocks.get(ins.dxf.name)
            if blk is None:
                continue
            parent = blk.name
            if blk.block_record.has_xdata("AcDbBlockRepBTag"):
                h = next(t.value for t in blk.block_record.get_xdata("AcDbBlockRepBTag") if t.code == 1005)
                parent = doc.entitydb[h].dxf.name
            if parent not in NAMES:
                continue
            state = {k: (round(v, 9) if isinstance(v, float) else v)
                     for k, v in db.read_dynamic_state(ins).items()}
            key = json.dumps([parent, state], sort_keys=True)
            counts[key] = counts.get(key, 0) + 1
            if key in cases or not ins.dxf.name.startswith("*U"):
                continue            # record a *U: a plain INSERT IS the definition
            mark = next(a for a in ins.attribs if a.dxf.tag == "MARK")
            case = {
                "block": parent, "state": state, "u": ins.dxf.name, "handle": ins.dxf.handle,
                "entities": [entity_geometry(e) for e in blk],
                "mark_align": _r(mark.dxf.align_point - ins.dxf.insert),
                "mark_invisible": int(mark.dxf.get("invisible", 0)),
            }
            if parent in desc_props:
                d = next(a for a in ins.attribs if a.dxf.tag == "DESCRIPTION")
                text = d.virtual_mtext_entity().text if d.has_embedded_mtext_entity else d.dxf.text
                case["description"] = text
                case["description_lines"] = db.wrapped_line_count(doc, desc_props[parent], text)
            cases[key] = case
    raw = template.read_bytes()
    return {
        "source": {"file": template.name, "size": len(raw), "sha1": hashlib.sha1(raw).hexdigest(),
                   "made_by": "AutoCAD (the owner's template STR_TEMPLATE_AUG_2026.dwt), converted to DXF 2026-09-24"},
        "cases": list(cases.values()),
        "counts": counts,
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    data = build(Path(argv[0]))
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"{len(data['cases'])} distinct *U states, {sum(data['counts'].values())} instances -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
