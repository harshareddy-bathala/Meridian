"""Protocol conformance: the exact wire shapes ``docs/MSP-SPEC.md`` describes.

Separate from ``integration/`` on purpose. An integration test may assert that
registration works; a conformance test asserts that the **bytes** match what the
specification says — field names, error bodies, status codes, version handling —
because MSP is published for other people to implement and a specification the
reference implementation quietly diverges from is worse than no specification.

    uv run pytest -m msp_conformance

Read as a group, these files are what a third-party station implementation would
be tested against, so each one names the section of the specification it is
pinning rather than asserting a shape the reader has to go and look up.

The battery is not repeated per endpoint. Version handling, request limits and
the error-body shape are enforced by shared middleware, so they are pinned once —
version parsing against ``/time`` in ``test_time_endpoint.py``, size caps in
``test_request_limits.py`` — and the per-endpoint files cover what is particular
to each: its fields, its authentication, and its own malformed cases.

Everything collected here is marked ``msp_conformance`` by the
``pytest_collection_modifyitems`` hook in ``tests/conftest.py`` — not by
``pytestmark`` in this file, which pytest honours in test modules only and
silently ignores in a ``conftest.py``.
"""

from __future__ import annotations
