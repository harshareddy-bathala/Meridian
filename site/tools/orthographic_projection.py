"""Orthographic projection of a wireframe globe and a circular orbit around it.

The drawing behind every picture Meridian ships — the social card, the still
globe inside index.html, and the README banner — is the same construction: a
sphere seen from infinitely far away, a 15-degree graticule on it, and one
circular orbit inclined across it. This module is that construction, and the
three callers differ only in where they put the camera and how large they draw
the result.

It is pure geometry. Nothing here opens a file, imports an imaging library or
knows what a pixel is: a caller supplies its own mapping from projected
coordinates to its own frame, which is why a 1200x630 raster card and a
200-unit inline SVG can share every line of it.

Frames: input points are in Earth radii about the Earth's centre, with +x at
0 deg longitude on the equator, +z at the north pole. Output is the camera's
(depth, right, up) — right and up in Earth radii, depth positive towards the
camera. See docs/GLOSSARY.md.

Reference: Snyder, Map Projections — A Working Manual, USGS PP 1395, p. 145
(the orthographic projection). The circular-orbit parameterisation is the
standard one: argument of latitude measured from the ascending node, rotated
by inclination and then by right ascension of that node.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

DEG = math.pi / 180
TAU = math.tau

Point = tuple[float, float, float]
Screen = tuple[float, float]


@dataclass(frozen=True)
class Camera:
    """Where the viewer stands, as the sub-observer point on the sphere.

    An orthographic camera has no position, only a direction: the point on the
    globe that faces the viewer squarely and sits at the centre of the disc.

    Attributes:
        latitude_rad: Sub-observer latitude, positive north.
        longitude_rad: Sub-observer longitude, positive east.
    """

    latitude_rad: float
    longitude_rad: float


@dataclass(frozen=True)
class CircularOrbit:
    """A circular orbit, which is all the pictures need.

    Attributes:
        raan_rad: Right ascension of the ascending node — where the orbit
            crosses the equator going north.
        inclination_rad: Angle between the orbit plane and the equator.
        radius_earth_radii: Orbit radius, in Earth radii rather than km, so it
            can be drawn directly against a unit sphere.
    """

    raan_rad: float
    inclination_rad: float
    radius_earth_radii: float


def unit_sphere_point(
    latitude_rad: float,
    longitude_rad: float,
    radius_earth_radii: float = 1.0,
) -> Point:
    """Geodetic latitude and longitude to a Cartesian point about Earth centre.

    Args:
        latitude_rad: Positive north.
        longitude_rad: Positive east.
        radius_earth_radii: Distance from the centre. 1.0 is the surface.

    Returns:
        (x, y, z) in Earth radii. Spherical Earth — these are pictures, not
        pass predictions, and the platform's own propagation does not go
        anywhere near this file.
    """
    ring_radius = math.cos(latitude_rad) * radius_earth_radii
    return (
        ring_radius * math.cos(longitude_rad),
        ring_radius * math.sin(longitude_rad),
        radius_earth_radii * math.sin(latitude_rad),
    )


def project_orthographic(point_earth_radii: Point, camera: Camera) -> Point:
    """Project one point, as (depth, right, up).

    Args:
        point_earth_radii: A point about the Earth's centre.
        camera: The sub-observer point.

    Returns:
        (depth, right, up). Depth greater than zero is the near hemisphere;
        right and up are the screen axes, both in Earth radii, so a point on
        the limb has hypot(right, up) == 1.
    """
    x, y, z = point_earth_radii
    sin_lat, cos_lat = math.sin(camera.latitude_rad), math.cos(camera.latitude_rad)
    sin_lon, cos_lon = math.sin(camera.longitude_rad), math.cos(camera.longitude_rad)
    x_camera = x * cos_lon + y * sin_lon
    y_camera = -x * sin_lon + y * cos_lon
    return (
        x_camera * cos_lat + z * sin_lat,
        y_camera,
        -x_camera * sin_lat + z * cos_lat,
    )


def is_behind_limb(depth: float, right: float, up: float) -> bool:
    """True when the globe hides this point: far side, and inside the disc."""
    return depth < 0 and math.hypot(right, up) < 1


def visible_runs(
    polylines: Iterable[Sequence[Point]],
    camera: Camera,
    to_screen: Callable[[float, float], Screen],
    *,
    near: bool,
) -> list[list[Screen]]:
    """Project polylines and split them where they pass behind the limb.

    A meridian drawn straight through the globe would join the point where it
    disappears to the point where it reappears with a chord across the disc.
    Splitting into runs and drawing each separately is what makes the wireframe
    read as a sphere rather than as a flat net.

    Args:
        polylines: Sequences of points in Earth radii, each a continuous line.
        camera: The sub-observer point.
        to_screen: Maps a projected (right, up) pair to the caller's own frame.
        near: True keeps the runs in front of the globe, False the runs behind
            it — which callers draw dimmer, so the far side recedes.

    Returns:
        Runs of at least two points, in the caller's frame.
    """
    out: list[list[Screen]] = []
    for points in polylines:
        run: list[Screen] = []
        for point in points:
            depth, right, up = project_orthographic(point, camera)
            if is_behind_limb(depth, right, up) == near:
                if len(run) > 1:
                    out.append(run)
                run = []
                continue
            run.append(to_screen(right, up))
        if len(run) > 1:
            out.append(run)
    return out


def graticule(step_deg: int = 3) -> list[list[Point]]:
    """A 15-degree graticule, sampled every `step_deg` along each line.

    The sampling step trades file size against how polygonal the curves look.
    A chord subtending `step_deg` on a circle of radius R departs from it by
    R(1 - cos(step_deg / 2)): at 3 degrees and 268 px that is 0.09 px, and at
    15 degrees and 86 px it is 0.74 px. A raster can afford 3; an SVG that
    ships inside a page cannot.

    Returns:
        Meridians every 15 degrees of longitude, then parallels every 15
        degrees of latitude, each as a list of surface points.
    """
    lines = []
    for lon_deg in range(-180, 180, 15):
        latitudes = range(-90, 91, step_deg)
        lines.append([unit_sphere_point(lat * DEG, lon_deg * DEG) for lat in latitudes])
    for lat_deg in range(-75, 76, 15):
        longitudes = range(-180, 181, step_deg)
        lines.append(
            [unit_sphere_point(lat_deg * DEG, lon * DEG) for lon in longitudes]
        )
    return lines


def satellite_position(orbit: CircularOrbit, argument_of_latitude_rad: float) -> Point:
    """One point on a circular orbit, measured from the ascending node."""
    cos_incl = math.cos(orbit.inclination_rad)
    sin_incl = math.sin(orbit.inclination_rad)
    cos_raan, sin_raan = math.cos(orbit.raan_rad), math.sin(orbit.raan_rad)
    cos_u = math.cos(argument_of_latitude_rad)
    sin_u = math.sin(argument_of_latitude_rad)
    return (
        orbit.radius_earth_radii * (cos_raan * cos_u - sin_raan * sin_u * cos_incl),
        orbit.radius_earth_radii * (sin_raan * cos_u + cos_raan * sin_u * cos_incl),
        orbit.radius_earth_radii * sin_u * sin_incl,
    )


def orbit_track(orbit: CircularOrbit, steps: int = 360) -> list[Point]:
    """The whole orbit as one closed polyline of `steps` segments."""
    return [satellite_position(orbit, i / steps * TAU) for i in range(steps + 1)]


def elevation_rad(satellite_point: Point, station_point: Point) -> float:
    """Elevation of a satellite above a station's local horizon.

    The angle between the station's local vertical — which on a sphere is the
    station's own position vector — and the line from the station to the
    satellite, less 90 degrees. Negative means below the horizon.

    Args:
        satellite_point: In Earth radii about the Earth's centre.
        station_point: The station, on the surface, same frame.

    Returns:
        Elevation in radians.
    """
    line = tuple(satellite_point[i] - station_point[i] for i in range(3))
    length = math.hypot(*line)
    along_vertical = sum(station_point[i] * line[i] for i in range(3))
    return math.asin(along_vertical / length)


def svg_path_data(runs: Iterable[Sequence[Screen]]) -> str:
    """SVG path data for a set of runs, one moveto per run.

    Coordinates are rounded to one decimal, and the pairs after the first take
    SVG's implicit-lineto form — which is what keeps a whole graticule inside
    a page rather than beside it.
    """
    parts = []
    for run in runs:
        head, *tail = run
        pairs = " ".join(f"{x:.1f} {y:.1f}" for x, y in tail)
        parts.append(f"M{head[0]:.1f} {head[1]:.1f}L{pairs}")
    return "".join(parts)
