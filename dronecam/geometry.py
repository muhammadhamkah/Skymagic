"""Vector math, camera orientation helpers, and local<->GPS conversion.

Coordinate convention used throughout ``dronecam`` (the *world* frame):

* ``x`` = East   (metres)
* ``y`` = North  (metres)
* ``z`` = Up     (metres)

Angles:

* ``yaw``   is measured around the world ``+z`` axis. ``yaw = 0`` points along
  ``+x`` (East) and increases towards ``+y`` (North), i.e. counter-clockwise
  when viewed from above. Radians internally, degrees at the I/O edges.
* ``pitch`` (gimbal tilt) is the elevation of the camera forward axis above the
  horizon. ``0`` is level, ``-pi/2`` looks straight down, ``+pi/2`` straight up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

EARTH_RADIUS_M = 6_378_137.0  # WGS-84 equatorial radius


@dataclass(frozen=True)
class Vec3:
    """An immutable 3D vector with the arithmetic the planner needs."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)

    __rmul__ = __mul__

    def __truediv__(self, s: float) -> "Vec3":
        return Vec3(self.x / s, self.y / s, self.z / s)

    def dot(self, other: "Vec3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vec3") -> "Vec3":
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def length_sq(self) -> float:
        return self.dot(self)

    def normalized(self) -> "Vec3":
        n = self.length()
        if n < 1e-12:
            return Vec3(0.0, 0.0, 0.0)
        return self / n

    def as_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @staticmethod
    def from_iterable(values) -> "Vec3":
        vals = list(values)
        if len(vals) != 3:
            raise ValueError(f"expected 3 components, got {len(vals)}")
        return Vec3(float(vals[0]), float(vals[1]), float(vals[2]))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_vec(a: Vec3, b: Vec3, t: float) -> Vec3:
    return Vec3(lerp(a.x, b.x, t), lerp(a.y, b.y, t), lerp(a.z, b.z, t))


def wrap_angle(a: float) -> float:
    """Wrap an angle (radians) into the range ``(-pi, pi]``."""
    return math.atan2(math.sin(a), math.cos(a))


def angle_diff(a: float, b: float) -> float:
    """Smallest signed difference ``a - b`` in radians, range ``(-pi, pi]``."""
    return wrap_angle(a - b)


def lerp_angle(a: float, b: float, t: float) -> float:
    """Interpolate angles along the shortest arc."""
    return wrap_angle(a + angle_diff(b, a) * t)


def look_at_angles(eye: Vec3, target: Vec3) -> Tuple[float, float]:
    """Return ``(yaw, pitch)`` in radians so the camera at ``eye`` faces ``target``."""
    d = target - eye
    horiz = math.hypot(d.x, d.y)
    yaw = math.atan2(d.y, d.x)
    pitch = math.atan2(d.z, horiz)
    return yaw, pitch


def forward_vector(yaw: float, pitch: float) -> Vec3:
    """Unit forward direction from yaw/pitch (radians)."""
    cp = math.cos(pitch)
    return Vec3(cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))


def camera_basis(yaw: float, pitch: float) -> Tuple[Vec3, Vec3, Vec3]:
    """Return orthonormal ``(forward, right, up)`` for a camera pose.

    Uses world up = ``+z``. Near the gimbal singularity (looking straight up or
    down) we fall back to deriving ``right`` from the yaw so the basis stays
    well-defined.
    """
    forward = forward_vector(yaw, pitch)
    world_up = Vec3(0.0, 0.0, 1.0)
    right = world_up.cross(forward)
    if right.length_sq() < 1e-9:
        # Looking nearly straight up/down: pick right from yaw alone.
        right = Vec3(-math.sin(yaw), math.cos(yaw), 0.0)
    right = right.normalized()
    up = forward.cross(right).normalized()
    return forward, right, up


@dataclass(frozen=True)
class GeoOrigin:
    """Maps the local metric world frame onto real-world GPS coordinates.

    ``heading_deg`` rotates the local frame about the vertical axis: it is the
    compass bearing (clockwise from true North) of the local ``+y`` axis. With
    ``heading_deg = 0`` local ``+y`` is North and local ``+x`` is East.
    """

    latitude: float
    longitude: float
    altitude: float = 0.0
    heading_deg: float = 0.0

    def to_gps(self, local: Vec3) -> Tuple[float, float, float]:
        """Convert a local ``Vec3`` (metres, E/N/U) to ``(lat, lon, alt)``."""
        h = math.radians(self.heading_deg)
        # Rotate local E/N by the heading so +y aligns with the chosen bearing.
        east = local.x * math.cos(h) + local.y * math.sin(h)
        north = -local.x * math.sin(h) + local.y * math.cos(h)
        lat0 = math.radians(self.latitude)
        dlat = (north / EARTH_RADIUS_M) * (180.0 / math.pi)
        dlon = (east / (EARTH_RADIUS_M * math.cos(lat0))) * (180.0 / math.pi)
        return (
            self.latitude + dlat,
            self.longitude + dlon,
            self.altitude + local.z,
        )
