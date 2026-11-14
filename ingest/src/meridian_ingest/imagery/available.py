"""Whether this installation can render imagery, said by name when it cannot.

The libraries are an extra (D-371), as HDF5 is (D-226): a deployment that never
renders a scene never carries GDAL. A missing extra is refused before any work
starts, with the command that installs it, rather than as an ``ImportError``
from inside a render halfway through a refresh.

Reference: docs/DECISIONS.md D-226, D-371.
"""

from __future__ import annotations

import importlib.util

__all__ = ["EXTRA", "LIBRARIES", "ImageryUnavailableError", "require_imagery"]

EXTRA = "meridian-ingest[imagery]"
LIBRARIES = ("rasterio", "numpy", "PIL")
"""The import names the extra provides, in the order a refusal names them."""


class ImageryUnavailableError(RuntimeError):
    """The imagery extra is not installed, so nothing can be read or drawn."""


def require_imagery() -> None:
    """Refuse, naming the extra, unless every imagery library can be imported.

    Raises:
        ImageryUnavailableError: One or more of :data:`LIBRARIES` is missing.
    """
    missing = [name for name in LIBRARIES if importlib.util.find_spec(name) is None]
    if missing:
        message = (
            f"imagery needs {', '.join(missing)}, which this installation lacks: "
            f"install {EXTRA} (D-371)"
        )
        raise ImageryUnavailableError(message)
