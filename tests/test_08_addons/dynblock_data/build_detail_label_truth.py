"""Build ``detail_label_truth.json`` - the CAD-made DETAIL LABEL ground truth.

Source: the owner's structural template ``STR_TEMPLATE_AUG_2026.dwt`` (the 2026-09-25
save, 16 788 603 bytes) copied and converted to DXF with
``studio.drafting.accore.dwg_to_dxf`` on 2026-09-29
(LIB-DRAFTING-DETAIL-MARKER-DYNAMIC-1).  It carries three AutoCAD-placed
DETAIL LABEL instances (layouts BLOCKWORK / S1 - A3 / WHS HAZARD, handles
10C99D / E2A1 / 1101A25), all sharing ``*U15`` = "Line Length" 49.0 mm.  The
record format is :mod:`build_section_marker_truth`'s (raw DXF pairs of the
``*U`` entities, the INSERTs and their representation XRECORDs).

The template is 109 MB as DXF, so it is not committed; this script is the
provenance.

Usage::

    python tests/fixtures/dynamic_blocks/build_detail_label_truth.py TEMPLATE.dxf
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "detail_label_truth.json"
NAME = "DETAIL LABEL"
MADE_BY = "AutoCAD (the owner's template STR_TEMPLATE_AUG_2026.dwt, 2026-09-25 save), converted to DXF 2026-09-29"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    sys.path.insert(0, str(HERE))
    from build_section_marker_truth import build

    data = build(Path(argv[0]), NAME)
    data["source"]["made_by"] = MADE_BY
    OUT.write_text(json.dumps(data, indent=0), encoding="utf-8")
    print(f"{len(data['instances'])} instances, {len(data['u_blocks'])} *U blocks -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
