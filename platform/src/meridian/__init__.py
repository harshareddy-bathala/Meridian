"""Meridian platform.

Module boundaries are firm — see docs/ARCHITECTURE.md. In particular:

* only ``meridian.orbit`` imports propagation libraries;
* only ``meridian.reliability`` decides what counts as a miss;
* ``meridian.scheduler`` consumes predictions, never the observation store;
* ``meridian.api`` is thin — validation and serialisation, no business logic.

The distribution directory is ``platform/``, but this package is ``meridian``.
``platform`` is a standard library module name and must never become an import
package here. See docs/DECISIONS.md D-012.
"""

__version__ = "0.1.0"
