"""The feature code and its version move together (D-255).

``FEATURE_VERSION`` is written into every model, and a model is scored only by
the feature code it was fitted on. That holds only if the version changes
whenever the code does. This test pins a digest of the feature code to each
version: a change to what a feature computes, made without a new version,
fails here and says what to do.

The digest is of each module's syntax tree with its docstrings removed, so
rewording an explanation or reformatting a line does not move it, and changing
a number, a name or a branch does. Which modules are feature code is read from
what :mod:`meridian.prediction.features` imports, so a module that joins it is
noticed too.

Each claim has a positive control.

Reference: docs/DECISIONS.md D-255.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from meridian.prediction.features import FEATURE_VERSION

PREDICTION = Path(__file__).resolve().parents[2] / "platform/src/meridian/prediction"

FEATURE_CODE = (
    "conditions",
    "feature_rows",
    "features",
    "geometry",
    "history",
    "profiles",
)
"""The modules that decide a feature's value."""

NOT_FEATURE_CODE = ("score",)
"""Imported by the feature code for the model it checks, not to compute."""

PINNED = {
    "features-1": "8fcdddb03fb54520606b5caa86bc525d1d76f5e04b7b2404cd7fc46e80485706",
}
"""Each version, and the digest of the feature code it names. A new version is
a new line; an old one is never edited, so it still says what it was."""


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            node.body = [  # type: ignore[attr-defined]
                one
                for one in body
                if not (
                    isinstance(one, ast.Expr)
                    and isinstance(one.value, ast.Constant)
                    and isinstance(one.value.value, str)
                )
            ] or [ast.Pass()]
    return tree


def digest(sources: dict[str, str]) -> str:
    """One hash over every module's tree, docstrings removed, in name order."""
    hashed = hashlib.sha256()
    for name in sorted(sources):
        tree = _strip_docstrings(ast.parse(sources[name]))
        hashed.update(f"{name}\n{ast.dump(tree)}\n".encode())
    return hashed.hexdigest()


def _sources() -> dict[str, str]:
    return {
        name: (PREDICTION / f"{name}.py").read_text(encoding="utf-8")
        for name in FEATURE_CODE
    }


def _imported_prediction_modules(name: str) -> set[str]:
    tree = ast.parse((PREDICTION / f"{name}.py").read_text(encoding="utf-8"))
    prefix = "meridian.prediction."
    return {
        node.module.removeprefix(prefix)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith(prefix)
    }


def test_the_feature_code_is_what_features_reaches() -> None:
    reached, waiting = set(), ["features"]
    while waiting:
        name = waiting.pop()
        if name in reached or name in NOT_FEATURE_CODE:
            continue
        reached.add(name)
        waiting.extend(_imported_prediction_modules(name))

    assert reached == set(FEATURE_CODE)


def test_the_feature_code_is_the_code_its_version_names() -> None:
    assert FEATURE_VERSION in PINNED, (
        f"{FEATURE_VERSION} has no digest: add it to PINNED as {digest(_sources())!r}"
    )
    assert digest(_sources()) == PINNED[FEATURE_VERSION], (
        "the feature code changed and FEATURE_VERSION did not: bump it in"
        " meridian/prediction/features.py and add the new version to PINNED"
        f" as {digest(_sources())!r}, leaving the old line as it is"
    )


def test_a_change_to_what_a_feature_computes_moves_the_digest() -> None:
    sources = _sources()
    changed = dict(sources)
    changed["geometry"] = sources["geometry"] + "\nEXTRA = 1\n"

    assert digest(changed) != digest(sources)


def test_rewording_a_docstring_or_reformatting_does_not() -> None:
    sources = _sources()
    reworded = dict(sources)
    reworded["features"] = (
        sources["features"].replace('"""', '"""Reworded. ', 1) + "\n\n# a note\n"
    )

    assert digest(reworded) == digest(sources)
