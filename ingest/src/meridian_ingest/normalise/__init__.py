"""Turning one stored artefact into rows, with nothing else in reach.

A normaliser takes bytes and the manifest written beside them, read from the
raw store, and returns records. It takes no client, no adapter, no URL and no
clock — there is no argument through which it could fetch anything, which is
what makes Stage 14's completion gate provable rather than asserted (D-142).

The records it returns are :mod:`meridian_ingest.normalise.records`. They are
not store types: they carry no ``record_id`` and no ``archive_station_id``,
because those are assigned by the load and a normaliser that could set one
would be a normaliser whose output depended on what was already in the
database.

Reference: docs/DECISIONS.md D-139, D-140, D-142.
"""
