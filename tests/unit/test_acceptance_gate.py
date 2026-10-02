"""Stage 24's register, kept true by reading the roadmap and the register.

    The project is software-complete when all of the following are true.

``docs/ACCEPTANCE.md`` quotes every clause of the roadmap's Stage 24 and names
what proves it. This gate reads both files and checks the register against
the repository:

* **every clause, once** — each clause of every path is in the register exactly
  once, worded as the roadmap words it, and the register has none the roadmap
  lacks;
* **evidence that exists** — a cited test is a function in the file named; a
  cited CI step is a step of the job named in ``ci.yml``; a cited section is a
  heading of the document named; a cited path is a file;
* **a status that means something** — ``proven`` cites a test or a CI step,
  ``run`` cites a transcript or a document, and ``pending — Stage N`` names a
  stage the roadmap has.

The "Proven by" column of ``OPERATIONS.md`` § Failure recovery is held to the
same rule: every test and test file it names exists.

Every check has a positive control on a small text written here, so a check
that passes has not passed by reading nothing.

Reference: docs/DECISIONS.md D-254; docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md
Stage 24.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROADMAP = REPO_ROOT / "docs" / "SOFTWARE-IMPLEMENTATION-ROADMAP.md"
REGISTER = REPO_ROOT / "docs" / "ACCEPTANCE.md"
OPERATIONS = REPO_ROOT / "docs" / "OPERATIONS.md"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TESTS = REPO_ROOT / "tests"

STAGE = "Stage 24 — Final software acceptance"
STATUS = re.compile(r"^(proven|run|pending — Stage (\d+))$")

Clauses = dict[str, list[str]]
"""Each path's heading, and its clauses in order."""

Rows = dict[str, list[tuple[str, str, str]]]
"""Each path's heading, and its rows: clause, status, evidence."""


def _clause(line: str) -> str:
    return line.removeprefix("- ").strip().rstrip(";.").strip()


def roadmap_clauses(text: str) -> Clauses:
    """Every ``- `` line under each ``## `` heading of Stage 24."""
    found = re.search(rf"^# {STAGE}\n(.*?)^---$", text, re.S | re.M)
    assert found, f"the roadmap has no '# {STAGE}' section"
    clauses: Clauses = {}
    heading = ""
    for line in found.group(1).splitlines():
        if line.startswith("## "):
            heading = line.removeprefix("## ").strip()
            clauses[heading] = []
        elif line.startswith("- ") and heading:
            clauses[heading].append(_clause(line))
    return clauses


def register_rows(text: str) -> Rows:
    """Every table row under each ``## `` heading of the register."""
    rows: Rows = {}
    heading = ""
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line.removeprefix("## ").strip()
            rows[heading] = []
        elif line.startswith("| ") and heading:
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            if cells[0] in {"Clause", "---"} or set(cells[0]) <= {"-"}:
                continue
            assert len(cells) == 3, f"a row is not three cells: {line}"
            rows[heading].append((_clause(cells[0]), cells[1], cells[2]))
    return rows


def coverage_problems(clauses: Clauses, rows: Rows) -> list[str]:
    """Clauses missing, repeated, reworded, or invented, path by path."""
    problems = [
        f"path missing from the register: {one}" for one in clauses if one not in rows
    ]
    problems += [
        f"path not in the roadmap: {one}" for one in rows if one not in clauses
    ]
    for path, wanted in clauses.items():
        held = [row[0] for row in rows.get(path, [])]
        problems += [f"{path}: missing '{one}'" for one in wanted if one not in held]
        problems += [
            f"{path}: not in the roadmap '{one}'" for one in held if one not in wanted
        ]
        problems += [
            f"{path}: '{one}' appears {held.count(one)} times"
            for one in sorted(set(held))
            if held.count(one) > 1
        ]
    return problems


# --- evidence ---------------------------------------------------------------


@cache
def _functions(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )


@cache
def _every_test() -> frozenset[str]:
    return frozenset(
        name for path in TESTS.rglob("test_*.py") for name in _functions(path)
    )


def _ci_steps(text: str) -> dict[str, set[str]]:
    """Each job of a workflow, and the names of its steps."""
    jobs: dict[str, set[str]] = {}
    job = ""
    for line in text.splitlines():
        named = re.match(r"^  ([\w-]+):\s*$", line)
        if named:
            job = named.group(1)
            jobs[job] = set()
            continue
        step = re.match(r"^\s+- name: (.+?)\s*$", line)
        if step and job:
            jobs[job].add(step.group(1).strip("'\""))
    return jobs


def _headings(document: Path) -> set[str]:
    text = document.read_text(encoding="utf-8")
    return {
        line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")
    }


def _test_problem(token: str, root: Path) -> str | None:
    file, function = token.split("::", 1)
    path = root / file
    if not path.is_file():
        return f"no such test file: {file}"
    return None if function in _functions(path) else f"no such test: {token}"


def _ci_problem(token: str, _root: Path) -> str | None:
    _, job, step = token.split(" › ", 2)
    steps = _ci_steps(CI.read_text(encoding="utf-8")).get(job)
    if steps is None:
        return f"no such CI job: {job}"
    return None if step in steps else f"no such CI step in {job}: {step}"


def _section_problem(token: str, root: Path) -> str | None:
    document, section = token.split(" § ", 1)
    path = root / "docs" / document
    if not path.is_file():
        return f"no such document: {document}"
    return None if section in _headings(path) else f"no such section: {token}"


def _path_problem(token: str, root: Path) -> str | None:
    return None if (root / token).exists() else f"no such path: {token}"


KINDS = (
    ("test", lambda token: "::" in token),
    ("ci", lambda token: token.startswith("ci.yml › ")),
    ("section", lambda token: " § " in token),
    ("path", lambda token: " " not in token and ("/" in token or "." in token)),
)
"""What a cited token is, tried in order; anything else is prose."""


def _kind(token: str) -> str | None:
    return next((kind for kind, test in KINDS if test(token)), None)


CHECKS = {
    "test": _test_problem,
    "ci": _ci_problem,
    "section": _section_problem,
    "path": _path_problem,
}


def citation_problem(token: str, root: Path = REPO_ROOT) -> str | None:
    """Why one cited piece of evidence does not exist, or None if it does."""
    kind = _kind(token)
    return CHECKS[kind](token, root) if kind else None


def _status_problem(
    status: str, kinds: set[str | None], stages: set[int]
) -> str | None:
    matched = STATUS.match(status)
    stage = int(matched.group(2)) if matched and matched.group(2) else None
    problems = {
        f"unknown status {status!r}": not matched,
        "proven, but cites no test or CI step": status == "proven"
        and not kinds & {"test", "ci"},
        "run, but cites no transcript": status == "run"
        and not kinds & {"section", "path"},
        f"no Stage {stage} in the roadmap": stage is not None and stage not in stages,
    }
    return next((problem for problem, found in problems.items() if found), None)


def row_problems(rows: Rows, stages: set[int]) -> list[str]:
    """Every row's status means what it says, and its evidence exists."""
    problems = []
    for path, held in rows.items():
        for clause, status, evidence in held:
            tokens = re.findall(r"`([^`]+)`", evidence)
            found = [
                _status_problem(status, {_kind(one) for one in tokens}, stages),
                *(citation_problem(one) for one in tokens),
            ]
            problems += [f"{path}: '{clause}': {one}" for one in found if one]
    return problems


def _stages(text: str) -> set[int]:
    return {int(one) for one in re.findall(r"^# Stage (\d+) — ", text, re.M)}


# --- the register -----------------------------------------------------------


def test_every_clause_is_in_the_register_once_as_the_roadmap_words_it() -> None:
    clauses = roadmap_clauses(ROADMAP.read_text(encoding="utf-8"))
    rows = register_rows(REGISTER.read_text(encoding="utf-8"))

    assert sum(len(one) for one in clauses.values()) == 40
    assert coverage_problems(clauses, rows) == []


def test_every_row_cites_evidence_that_exists() -> None:
    rows = register_rows(REGISTER.read_text(encoding="utf-8"))
    stages = _stages(ROADMAP.read_text(encoding="utf-8"))

    assert row_problems(rows, stages) == []


def test_what_failure_recovery_says_proves_it_exists() -> None:
    text = OPERATIONS.read_text(encoding="utf-8")
    section = re.search(r"^## Failure recovery\n(.*?)^## ", text, re.S | re.M)
    assert section
    cited = [
        token
        for line in section.group(1).splitlines()
        if line.startswith("| **")
        for token in re.findall(r"`(test_[\w.]+)`", line.rsplit(" | ", 1)[1])
    ]
    files = {path.name for path in TESTS.rglob("test_*.py")}

    assert len(cited) >= 10
    assert [
        one
        for one in cited
        if one not in (files if one.endswith(".py") else _every_test())
    ] == []


# --- positive controls ------------------------------------------------------

ROADMAP_TEXT = f"""# {STAGE}

## Operational path

- one thing holds;
- another thing holds.

---
"""


def _register(*rows: str) -> str:
    return "## Operational path\n\n| Clause | Status | Evidence |\n|---|---|---|\n" + (
        "\n".join(rows)
    )


PROVEN = (
    "`tests/unit/test_acceptance_gate.py::test_every_row_cites_evidence_that_exists`"
)


@pytest.mark.parametrize(
    ("register", "found"),
    [
        (_register(f"| one thing holds | proven | {PROVEN} |"), "missing 'another"),
        (
            _register(
                f"| one thing holds | proven | {PROVEN} |",
                f"| one thing holds | proven | {PROVEN} |",
                f"| another thing holds | proven | {PROVEN} |",
            ),
            "appears 2 times",
        ),
        (
            _register(
                f"| one thing holds | proven | {PROVEN} |",
                f"| another thing mostly holds | proven | {PROVEN} |",
            ),
            "not in the roadmap 'another thing mostly holds'",
        ),
    ],
)
def test_a_clause_left_out_repeated_or_reworded_is_found(
    register: str, found: str
) -> None:
    problems = coverage_problems(roadmap_clauses(ROADMAP_TEXT), register_rows(register))

    assert any(found in one for one in problems), problems


@pytest.mark.parametrize(
    ("row", "found"),
    [
        (
            "| a | proven | `tests/unit/test_acceptance_gate.py::test_nothing` |",
            "no such test",
        ),
        ("| a | proven | `tests/unit/test_nowhere.py::test_x` |", "no such test file"),
        ("| a | proven | `ci.yml › image › A step nobody wrote` |", "no such CI step"),
        ("| a | proven | `ci.yml › nowhere › Tear down` |", "no such CI job"),
        ("| a | run | `OPERATIONS.md § A section nobody wrote` |", "no such section"),
        ("| a | run | `deploy/tools/nothing.py` |", "no such path"),
        ("| a | proven | `OPERATIONS.md § Failure recovery` |", "cites no test"),
        ("| a | run | `meridian report build` |", "cites no transcript"),
        (f"| a | pending — Stage 99 | {PROVEN} |", "no Stage 99"),
        (f"| a | done | {PROVEN} |", "unknown status"),
    ],
)
def test_evidence_that_is_missing_or_does_not_fit_its_status_is_found(
    row: str, found: str
) -> None:
    problems = row_problems(register_rows(_register(row)), {24})

    assert any(found in one for one in problems), problems


def test_evidence_that_exists_raises_nothing() -> None:
    rows = register_rows(
        _register(
            f"| a | proven | {PROVEN}; `ci.yml › image › Tear down` |",
            "| b | run | `OPERATIONS.md § Failure recovery`;"
            " `deploy/tools/long_run.py` |",
            "| c | pending — Stage 24 | |",
        )
    )

    assert row_problems(rows, {24}) == []
