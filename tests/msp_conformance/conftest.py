"""Protocol conformance: exact wire shapes, asserted against golden fixtures.

Separate from ``integration/`` on purpose. An integration test may assert that
registration works; a conformance test asserts that the **bytes** match what
``docs/MSP-SPEC.md`` says — field names, error bodies, status codes, version
handling — because MSP is published for other people to implement and a
specification the reference implementation quietly diverges from is worse than no
specification.

    uv run pytest -m msp_conformance

The same fixtures are what a third-party station implementation would be tested
against, which is the reason they are golden files rather than assertions
scattered through the endpoint tests.

**Populated from Stage 3**, where the shared version parsing, error shape and
request limits land. Every endpoint gets the same battery: missing version,
malformed version, unsupported major, malformed JSON, wrong field type, internal
failure, exact error body, and no secret in any response.

Everything collected here is marked ``msp_conformance`` by the
``pytest_collection_modifyitems`` hook in ``tests/conftest.py`` — not by
``pytestmark`` in this file, which pytest honours in test modules only and
silently ignores in a ``conftest.py``.

This file exists so the directory survives a clone — git does not track empty
directories.
"""

from __future__ import annotations
