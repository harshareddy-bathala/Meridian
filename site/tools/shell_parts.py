"""What shell.py writes into every page: the masthead, the nav, the footer, giving.

Kept apart from shell.py so that the words a visitor reads are here and in
`shell/*.html`, and the machinery that places them is in shell.py.

The nav is ordered by what a visitor came for (D-342): how it works, the data,
running a station, supporting it, and, last, building on it. Architecture, the
protocol, the documents index and the terms sit under Developers, and the nav
marks that section as current on each of them.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "shell"

REPO = "https://github.com/harshareddy-bathala/Meridian"
DASHBOARD = "https://dash.meridian.org.in/"

NAV = [
    ("/how-it-works/", "How it works"),
    ("/data/", "Data"),
    ("/stations/", "Stations"),
    ("/support/", "Support"),
    ("/developers/", "Developers"),
]

# Pages that belong to a nav section without being its own page.
SECTION = {
    "areas/index.html": "/data/",
    "architecture/index.html": "/developers/",
    "protocol/index.html": "/developers/",
    "docs/index.html": "/developers/",
    "terms/index.html": "/developers/",
}


def nav_current(page: str) -> str | None:
    """The nav entry a page is, or sits under; None for the home page and 404."""
    own = "/" + page.removesuffix("index.html")
    if any(href == own for href, _ in NAV):
        return own
    return SECTION.get(page)


def masthead(cls: str, nav: str) -> str:
    """The masthead, with its class and its rendered nav links."""
    template = (TEMPLATES / "masthead.html").read_text(encoding="utf-8")
    return template.rstrip("\n").format(cls=cls, nav=nav)


def footer() -> str:
    """The wide footer every page ends on."""
    template = (TEMPLATES / "footer.html").read_text(encoding="utf-8")
    return template.rstrip("\n").format(repo=REPO, dashboard=DASHBOARD)


# ------------------------------------------------------------ ways to give --
# The support page's payment block, from site/support.json. A value that is not
# set renders as "being set up", never as a placeholder: a published UPI ID
# that belongs to nobody, or to the wrong person, sends someone's money there.

UPI_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,255}@[a-z][a-z0-9]{1,63}")
SPONSORS = re.compile(r"https://github\.com/sponsors/[A-Za-z0-9][A-Za-z0-9-]{0,38}")
PLACEHOLDER = re.compile(r"example|placeholder|xxx|your|todo|changeme|test", re.I)
UPI_QR = "support/upi-qr.svg"


def _unreal(value: object, shape: re.Pattern[str]) -> bool:
    """True for a value that is set and is not a real one."""
    return value is not None and (
        not isinstance(value, str)
        or not shape.fullmatch(value)
        or bool(PLACEHOLDER.search(value))
    )


def support_problems(data: dict[str, object]) -> list[str]:
    """Why support.json cannot be published as it stands, if it cannot."""
    problems = []
    if _unreal(data.get("upi_id"), UPI_ID):
        problems.append(f"support.json: upi_id {data['upi_id']!r} is not a real UPI ID")
    if _unreal(data.get("github_sponsors"), SPONSORS):
        problems.append(
            f"support.json: github_sponsors {data['github_sponsors']!r} "
            "is not a Sponsors profile URL"
        )
    return problems


OPENING = """          <span class="pill pill-wait">Being set up</span>"""


def give(data: dict[str, object], qr_present: bool) -> str:
    """The block of ways to give, for what support.json has."""
    upi, sponsors = data.get("upi_id"), data.get("github_sponsors")
    if upi is None and sponsors is None:
        return """      <div class="empty">
        <p class="empty-t">GitHub Sponsors and UPI are being set up.</p>
        <p>They will appear here. Until then, to support Meridian, write to
        <a href="mailto:hello@meridian.org.in">hello@meridian.org.in</a>.</p>
      </div>"""

    sponsor_line = (
        f'          <p><a href="{sponsors}">Sponsor on GitHub</a></p>'
        if sponsors is not None
        else OPENING
    )
    tiles = [
        f"""        <li class="tile">
          <h3>GitHub Sponsors</h3>
          <p>Monthly or once, by card, from anywhere.</p>
{sponsor_line}
        </li>"""
    ]
    if upi is not None:
        if not qr_present:
            raise SystemExit(f"support.json sets upi_id but site/{UPI_QR} is missing")
        tiles.append(f"""        <li class="tile">
          <h3>UPI, in India</h3>
          <p>Scan with any UPI app, or type the ID. You choose the amount.</p>
          <img class="qr" src="/{UPI_QR}" width="176" height="176"
            alt="UPI QR code for {upi}">
          <p><code>{upi}</code></p>
        </li>""")
    else:
        tiles.append(f"""        <li class="tile">
          <h3>UPI, in India</h3>
          <p>Scan with any UPI app, or type the ID.</p>
{OPENING}
        </li>""")
    return '      <ul class="tiles">\n' + "\n".join(tiles) + "\n      </ul>"
