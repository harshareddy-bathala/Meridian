"""Meridian — predictive scheduling and reliability for satellite ground stations.

The subpackages:

* ``api`` — the public and MSP HTTP surface: validation and serialisation, with
  the decisions themselves taken a layer down;
* ``registry`` — stations, their credentials and their derived health;
* ``orbit`` — propagation, element sets and pass geometry. Propagation is
  confined here, so the rest of the platform depends on this interface rather
  than on a particular propagator;
* ``store`` — the SQL access layer everything above reaches the database
  through;
* ``config`` — settings, read from the environment once at startup.

Four subpackages are documented interfaces with no implementation behind them
yet: ``prediction`` and ``scheduler`` (Phase 2), ``reliability`` (Phase 3) and
``observations`` (Stage 9). Each states what it will provide and what it will
not touch.

Which module may import what is set out in docs/ARCHITECTURE.md.

The distribution directory is ``platform/`` while the import package is
``meridian``: ``platform`` is a standard library module name, and a package of
that name here would shadow it and raise ``AttributeError`` from inside
third-party libraries at import time (D-012).
"""

__version__ = "0.1.0"
