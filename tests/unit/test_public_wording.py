"""Every tracked text file reads well to a stranger: no sums, no exam process.

The repository is public, and a sponsor or contributor may read it before the
site. Four kinds of line read badly there, and each is refused here:

- **a sum.** Funding is described by what it pays for, never by an amount, so a
  figure cannot date the page or read as a target;
- **the setting of a review rather than its reason.** A decision says why, and
  that reason holds wherever it is read;
- **a citation of ``CLAUDE.local.md``**, a gitignored file no reader can open.
  The rules it held are ``docs/CODE-STANDARDS.md``;
- **a record of who asked**, rather than the reason a reader can check.

The project's origin is stated once, honestly, in the README, the project
document and the site's About page. Those lines, and two comments in migrations
that are never edited once merged (``GIT-WORKFLOW.md``), are the allowed
exceptions, each matched exactly.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

TEXT_SUFFIXES = {
    ".md", ".html", ".json", ".toml", ".yml", ".yaml", ".txt", ".example",
    ".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".sql", ".svg", ".cff",
}  # fmt: skip

# Lockfiles hold integrity hashes, whose base64 can spell anything.
SKIPPED = re.compile(r"(^|/)(package-lock\.json|uv\.lock)$|^LICENSE$")

THIS_FILE = "tests/unit/test_public_wording.py"

RULES = {
    "sum": re.compile(r"₹ ?\d|\bINR ?\d|\bRs\.? ?\d|\b(lakh|crore)s?\b", re.I),
    "review setting": re.compile(
        r"\bviva\b|\bfaculty\b|\bsynopsis\b|\bexaminers?\b|\bsemesters?\b"
        r"|\bcoursework\b|\bfinal[- ]year\b|\bcollege\b|\bstudent credit\b",
        re.I,
    ),
    "unreadable citation": re.compile(r"CLAUDE\.local\.md"),
    "who asked": re.compile(
        r"\bthe user (chose|said|asked for|wanted|decided)\b|\bteam's choice\b",
        re.I,
    ),
}

# Path, then the exact line, then why it stays.
ALLOWED = {
    (
        "README.md",
        "Meridian began as a final-year engineering project, built by a team of"
        " three, and is written to outlast it.",
    ): "the project's origin, said once",
    (
        "docs/PROJECT.md",
        "A final-year engineering project, built by a team of three",
    ): "the project's origin, in its own document",
    (
        "site/about/index.html",
        "Meridian began as a final-year engineering project, built by a team of"
        " three, and is written to outlast it.</p>",
    ): "the project's origin, on the page about the project",
    (
        ".gitignore",
        "CLAUDE.local.md",
    ): "ignores a local file; cites nothing",
    (
        "deploy/migrations/sql/0007_schema_corrections.sql",
        "-- CLAUDE.local.md §4 permits no abbreviation that is not in"
        " docs/GLOSSARY.md,",
    ): "a merged migration is never edited",
    (
        "deploy/migrations/sql/0016_archive_ingest.sql",
        "-- question asked in a viva, and it should be one query rather than a join",
    ): "a merged migration is never edited",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line_number: int
    rule: str
    line: str


def tracked_text_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        path
        for path in listed
        if (Path(path).suffix in TEXT_SUFFIXES or path == ".gitignore")
        and not SKIPPED.search(path)
        and path != THIS_FILE
    ]


def findings_in(path: str, text: str) -> list[Finding]:
    found: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if (path, stripped) in ALLOWED:
            continue
        found.extend(
            Finding(path, line_number, rule, stripped[:160])
            for rule, pattern in RULES.items()
            if pattern.search(line)
        )
    return found


def test_no_tracked_file_reads_badly_to_a_stranger() -> None:
    paths = tracked_text_files()
    assert len(paths) > 500, "the scan must read the repository, not nothing"
    found = [
        finding
        for path in paths
        if (REPO_ROOT / path).is_file()
        for finding in findings_in(path, (REPO_ROOT / path).read_text("utf-8"))
    ]
    assert not found, "\n".join(
        f"{f.path}:{f.line_number}: {f.rule}: {f.line}" for f in found
    )


@pytest.mark.parametrize(
    ("rule", "planted"),
    [
        ("sum", "Tier 1 costs ₹24,300."),
        ("sum", "about Rs. 9,200 each"),
        ("sum", "two lakh in total"),
        ("review setting", "The panel asked this at the viva."),
        ("review setting", "funded by student credit"),
        ("review setting", "reachable from outside the college network"),
        ("unreadable citation", "# per CLAUDE.local.md §2"),
        ("who asked", "The user chose it on 2026-10-01."),
        ("who asked", "begun early, by the team's choice"),
    ],
)
def test_each_rule_catches_a_planted_line(rule: str, planted: str) -> None:
    found = findings_in("docs/planted.md", f"A clean line.\n{planted}\n")
    assert [(f.line_number, f.rule) for f in found] == [(2, rule)]


def test_an_allowed_line_is_allowed_only_in_its_own_file() -> None:
    (path, line), _reason = next(iter(ALLOWED.items()))
    assert findings_in(path, line) == []
    assert findings_in("docs/elsewhere.md", line) != []


@pytest.mark.parametrize(("path", "line"), list(ALLOWED))
def test_every_allowed_line_still_exists(path: str, line: str) -> None:
    lines = [
        each.strip() for each in (REPO_ROOT / path).read_text("utf-8").splitlines()
    ]
    assert line in lines, f"{path} no longer holds an allowed line; remove it here"
