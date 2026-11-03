# Code standards

The rules every module in this repository is written to. Most are checked by CI; where one is not, it says so. Comments and decisions cite them by section number, as `CODE-STANDARDS.md §2`. The numbering is fixed, so references made before this file was published still point at the right rule.

[`CONTRIBUTING.md`](../CONTRIBUTING.md) says how a change is shaped. [`GIT-WORKFLOW.md`](GIT-WORKFLOW.md) covers branches, commits and migrations. This file covers the code itself.

---

## 1. Every line can be explained

Code nobody on the team can walk through line by line is not committed, however well it works (`GIT-WORKFLOW.md` Rule 10). The rest of this file exists to keep code in a shape where that stays possible.

## 2. Size

A file or function that is too big to hold in your head is too big to review.

| Limit | Value | Checked by |
|---|---|---|
| Lines in a Python module | 400 | the CI step *No module over 400 lines* |
| Lines in a TypeScript module | 400 | eslint `max-lines` |
| Lines in a function | about 40; 60 in TypeScript, where JSX spends lines | eslint `max-lines-per-function`; review in Python |
| Parameters | four, five at most | ruff `PLR0913`, eslint `max-params` |
| Cyclomatic complexity | 8 | ruff `C901`, eslint `complexity` |
| Branches · statements · returns | 8 · 30 · 4 | ruff `PLR0912`, `PLR0915`, `PLR0911` |

**Past four parameters, take a dataclass.** A frozen dataclass names what the values are for, and a caller cannot pass two of them in the wrong order.

**Over a limit, extract a named helper; never compress.** Joining lines or shortening names to fit makes the code worse and the limit meaningless.

Tests are exempt from the module limit: a table-driven test file grows honestly with the cases it covers.

## 3. Computation apart from I/O

A function either works something out or talks to the outside world (a database, a socket, a file, the clock), not both. The pure half is then tested without a database, and the I/O half is thin enough to read at a glance.

The clock counts as I/O. A function that needs the time takes `now` as an argument.

## 4. Names

- **Whole words.** An abbreviation is allowed only if [`GLOSSARY.md`](GLOSSARY.md) lists it.
- **Units in the name** when a value has one: `elevation_deg`, `duration_s`, `frequency_hz`, `range_km`. Prometheus metrics do the same with their suffixes.
- **No shared constants module.** A value belongs beside the code that owns it, or such a module collects everything that has no other home.

ruff's `N` rules check the naming convention. The rest is checked in review.

## 5. Docstrings and constants

1. **A docstring says why**, and what a caller must know. It does not restate the signature.
2. **Every argument is annotated**, and its unit is in its name (§4). ruff's `ANN` rules check annotations, and `D` (Google convention) checks docstrings.
3. **Text that helps the reader beats structure that hides it.** An error message that names the setting, the reason and the command that fixes it stays at the raise site (ruff's `TRY003` is off for this reason).
4. **Every constant states where it came from**: a specification, a measurement, a decision (`D-NNN`) or a datasheet. A number with no stated source is not committed.

Tests are exempt from docstring rules. A test's name is its docstring.

## 6. Time

**A naive `datetime` anywhere is a bug, not a tolerance.** Every timestamp is timezone-aware and in UTC: in Python, in the database, on the wire. ruff's `DTZ` rules ban the calls that create naive values, and functions that receive a datetime refuse a naive one.

## 7. Errors and hygiene

- Catch the narrowest exception that can actually occur, and say what to do about it.
- No commented-out code (ruff `ERA`). Version control remembers it.
- `pathlib`, not `os.path` (ruff `PTH`).
- No stray `print()` (ruff `T20`). Command-line output is a deliberate, marked exemption.

## 8. Tests

- Every change that alters behaviour carries a test that fails without it.
- Model evaluation uses temporal splits only, never a shuffled split (`CLAUDE.md` rule 6).
- Integration tests run against TimescaleDB, never SQLite.

## 9. Tooling

All of the above that a machine can check is in [`pyproject.toml`](../pyproject.toml), [`dashboard/eslint.config.js`](../dashboard/eslint.config.js) and [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), and runs on every pull request:

- **ruff**, line length 88, with `E W F I N D ANN C90 PL SIM RET ARG TRY PTH ERA DTZ TID T20 UP B RUF`;
- **mypy** in `strict` mode, plus `disallow_any_explicit` and `warn_unreachable`;
- **eslint** with the TypeScript strict type-checked set, restating §2's limits with the same numbers (D-081);
- the **module-length** step, the one limit ruff cannot express.

D-044 records why each rule is in the set and the few that are deliberately off.
