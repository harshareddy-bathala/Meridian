"""The nine Stage 31 sources, as the registry lists them.

Each row pairs an adapter with its normaliser and says how the source is
reached. Limits are the source's own published figures, and only where we
could read them; the per-fetch request budget applies to every source either
way (D-223). Every row is disabled by default (D-220).

Reference: docs/DECISIONS.md D-220, D-223, D-225.
"""

from __future__ import annotations

from meridian_ingest.adapters.public.bhuvan import BhuvanAdapter
from meridian_ingest.adapters.public.black_marble import (
    BlackMarbleAdapter,
    BlackMarbleNormaliser,
)
from meridian_ingest.adapters.public.firms import FirmsAdapter, FirmsNormaliser
from meridian_ingest.adapters.public.gibs import GibsAdapter
from meridian_ingest.adapters.public.open_meteo import (
    OPEN_METEO_AEROSOL,
    OPEN_METEO_CLOUD,
    HourlyAdapter,
    HourlyNormaliser,
)
from meridian_ingest.adapters.public.ornl_ndvi import (
    OrnlNdviAdapter,
    OrnlNdviNormaliser,
)
from meridian_ingest.adapters.public.power_precipitation import (
    PowerAdapter,
    PowerNormaliser,
)
from meridian_ingest.adapters.public.swpc_kp import SwpcKpAdapter, SwpcKpNormaliser
from meridian_ingest.adapters.public.tiles import DisplayOnlyNormaliser
from meridian_ingest.adapters.registration import Registration
from meridian_ingest.rate_ledger import RateWindow

__all__ = ["OPEN_METEO_LIMITS", "PUBLIC"]

HOUR, DAY = 3_600, 86_400

OPEN_METEO_LIMITS = (
    RateWindow(600, 60),
    RateWindow(5_000, HOUR),
    RateWindow(10_000, DAY),
)
"""The free API's published limits, per client, shared by both endpoints."""

PUBLIC: tuple[Registration, ...] = (
    Registration(SwpcKpAdapter(), SwpcKpNormaliser(), cadence_s=HOUR),
    Registration(
        HourlyAdapter(OPEN_METEO_CLOUD),
        HourlyNormaliser(OPEN_METEO_CLOUD),
        rate_limits=OPEN_METEO_LIMITS,
        ledger="open_meteo",
        cadence_s=HOUR,
    ),
    Registration(
        HourlyAdapter(OPEN_METEO_AEROSOL),
        HourlyNormaliser(OPEN_METEO_AEROSOL),
        rate_limits=OPEN_METEO_LIMITS,
        ledger="open_meteo",
        cadence_s=3 * HOUR,
    ),
    Registration(GibsAdapter(), DisplayOnlyNormaliser("gibs-display-1"), cadence_s=DAY),
    Registration(
        FirmsAdapter(),
        FirmsNormaliser(),
        rate_limits=(RateWindow(5_000, 600),),
        key_env="FIRMS_MAP_KEY",
        cadence_s=3 * HOUR,
        lookback_s=2 * DAY,
    ),
    Registration(
        OrnlNdviAdapter(), OrnlNdviNormaliser(), cadence_s=DAY, lookback_s=48 * DAY
    ),
    Registration(PowerAdapter(), PowerNormaliser(), cadence_s=DAY, lookback_s=14 * DAY),
    Registration(
        BlackMarbleAdapter(),
        BlackMarbleNormaliser(),
        key_env="EARTHDATA_TOKEN",
        cadence_s=DAY,
        lookback_s=62 * DAY,
    ),
    Registration(
        BhuvanAdapter(), DisplayOnlyNormaliser("bhuvan-display-1"), cadence_s=7 * DAY
    ),
)
