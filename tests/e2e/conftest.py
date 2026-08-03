"""End-to-end tests: the full compose stack, driven from outside.

Everything collected under this directory is marked ``e2e`` by the
``pytest_collection_modifyitems`` hook in ``tests/conftest.py``, so an individual
test module cannot forget to and leak a ten-minute compose bring-up into the unit
run.

    uv run pytest -m e2e

The marking is *not* done here with ``pytestmark``. That attribute is honoured in
test modules only; in a ``conftest.py`` it is silently ignored, and the marker
would look applied while selecting nothing.

This file exists so the directory survives a clone. Git does not track empty
directories, and the roadmap's four-way test layout is not a layout if two of its
four disappear on checkout.

**Populated at Stage 10**, whose gate is the first genuine end-to-end assertion:
``docker compose --profile sim up`` registers a virtual station, receives an
assignment, submits an observation, and keeps simulation provenance at every
layer.
"""

from __future__ import annotations
