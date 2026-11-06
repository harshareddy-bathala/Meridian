"""Draw the support page's UPI QR code from the UPI ID in site/support.json.

The code carries a UPI payment link with the ID and the payee's name and
nothing else: no amount, because the person paying chooses it (D-343). The
encoding is segno's, a QR library, rather than our own: a QR code is an
error-correcting code, and CLAUDE.md's first rule is not to write one of those.

segno is needed only here, at the moment the ID changes, so it is not a
dependency of anything:

Run:  uv run --no-project --with segno python site/tools/upi_qr.py
      then python site/tools/shell.py and python site/tools/stamp_assets.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from shell_parts import UPI_QR, support_problems

SITE = Path(__file__).resolve().parent.parent
PAYEE = "Meridian"


def main() -> int:
    """Write site/support/upi-qr.svg, or refuse if there is no real ID."""
    data = json.loads((SITE / "support.json").read_text(encoding="utf-8"))
    problems = support_problems(data)
    if problems or data.get("upi_id") is None:
        print("\n".join(problems) or "support.json has no upi_id yet", file=sys.stderr)
        return 1

    import segno  # noqa: PLC0415 - only this tool needs it, and only when run

    link = "upi://pay?" + urlencode({"pa": data["upi_id"], "pn": PAYEE, "cu": "INR"})
    out = SITE / UPI_QR
    segno.make(link, error="m").save(
        str(out),
        kind="svg",
        scale=8,
        border=2,
        dark="#08090D",
        light=None,
        xmldecl=False,
        svgns=True,
        title=f"UPI QR code for {data['upi_id']}",
    )
    # segno writes a width and height and no viewBox. Without one an <img>
    # drawn at any other size crops the code instead of scaling it.
    svg = out.read_text(encoding="utf-8")
    size = re.search(r'width="(\d+)" height="(\d+)"', svg)
    if size is None:
        raise SystemExit(f"{out}: no width and height to build a viewBox from")
    view = f'viewBox="0 0 {size.group(1)} {size.group(2)}" '
    out.write_text(svg.replace("<svg ", "<svg " + view, 1), encoding="utf-8")
    print(f"wrote {out.relative_to(SITE.parent)} for {data['upi_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
