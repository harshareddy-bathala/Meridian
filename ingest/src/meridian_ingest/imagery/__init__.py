"""Open imagery: a published area's clearest recent scene, placed and explained.

The one package in this distribution that decodes an image. It searches an
open catalogue for a scene over each published area, keeps the window it read
in the raw store, renders georeferenced files and a picture with latitude and
longitude drawn by our own code, places markers from records only, and
publishes the result as a content-addressed directory whose manifest is the
record (D-371 to D-375).

**What keeps D-133 true here.** An image library may be imported in this
package and nowhere else in ``meridian_ingest``, and this package imports
neither the loaders nor ``meridian.store``, so nothing it reads from a pixel
can become a stored sample. ``tests/unit/test_imagery_boundaries.py`` holds
both rules. Every number it publishes comes from a data product — a scene
classification layer, a FIRMS record, a regional report — never from the
picture it draws.

Its libraries are the ``meridian-ingest[imagery]`` extra. Importing this
package needs none of them; the modules that do call :func:`require_imagery`.

Reference: docs/DECISIONS.md D-133, D-138, D-371.
"""

from __future__ import annotations

from meridian_ingest.imagery.available import ImageryUnavailableError, require_imagery

__all__ = ["ImageryUnavailableError", "require_imagery"]
