-- 0014 — a station declares how precisely its location may be published
--
-- The roadmap's do-not-expose list ends with "unrestricted raw station
-- coordinates *if privacy policy later requires approximation*", a conditional
-- left for whichever stage published coordinates. Stage 11 is that stage, and
-- DECISIONS.md D-082 resolves it: the operator declares the precision, because
-- a university rooftop and a home address have different exposure and only the
-- operator knows which they have.
--
-- Decimal places rather than a distance in metres. Rounding is then one
-- operation with no geodesy in it — a metre figure needs a cos(latitude) term
-- and has to say what it means at the poles. At the equator: 1 ≈ 11 km,
-- 2 ≈ 1.1 km, 3 ≈ 110 m, 4 ≈ 11 m, 5 ≈ 1.1 m, 6 ≈ 11 cm. Longitude's true
-- ground distance shrinks with cos(latitude), so a declared figure is an upper
-- bound on what is disclosed and never a lower one — the safe direction for a
-- privacy control.
--
-- This column governs publication only. lat_deg and lon_deg keep every digit
-- the station sent, and `find_receiving_stations` keeps serving them at full
-- precision to pass generation and the scheduler. Rounding a stored coordinate
-- would shift predicted acquisition by seconds and schedule the station for a
-- place it is not.
--
-- The default is the backfill. One statement, applied by Postgres to every
-- station registered before the field existed, with no separate update and no
-- window in which the column is null. 2 sits at the conservative end on
-- purpose: it lands on operators who never read the specification and
-- therefore consented to nothing, and ~1.1 km is the right campus rather than
-- the right building.
--
-- The upper bound is 6 because that is finer than any coordinate an operator
-- types by hand — roughly 11 cm — so nothing real is lost by refusing 7. The
-- lower bound is 1 rather than 0: zero decimals is a whole degree, ~111 km,
-- which no longer identifies a station's site well enough to be worth
-- publishing at all.
alter table stations
    add column location_precision_decimals smallint not null default 2
        constraint station_location_precision_is_publishable
            check (location_precision_decimals between 1 and 6);

comment on column stations.location_precision_decimals is
    'Decimal places to round lat_deg and lon_deg to when publishing them. '
    'Publication only — the stored coordinates keep full precision and the '
    'scheduler reads them unrounded. See docs/DECISIONS.md D-082, MSP §4.1.';
