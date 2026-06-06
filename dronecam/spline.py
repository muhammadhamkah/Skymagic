"""Smooth interpolation helpers for hand-drawn camera paths.

A user drops a few rough waypoints; we fit a Catmull-Rom spline through them so
the camera glides smoothly through every point (the curve passes through the
control points, unlike a Bezier). Used by :mod:`dronecam.segments` to turn a
handful of waypoint Empties into a smooth flight.
"""

from __future__ import annotations

from typing import List

from .geometry import Vec3, lerp_vec


def smoothstep(u: float) -> float:
    """Ease in/out curve on ``[0, 1]`` (zero velocity at both ends)."""
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def catmull_rom_at(points: List[Vec3], u: float) -> Vec3:
    """Position at parameter ``u`` in ``[0, 1]`` along a Catmull-Rom spline.

    Degenerate cases are handled: 0 points -> origin, 1 point -> that point,
    2 points -> straight line. Endpoints are duplicated so the curve reaches the
    first and last waypoints exactly.
    """
    n = len(points)
    if n == 0:
        return Vec3()
    if n == 1:
        return points[0]
    if n == 2:
        return lerp_vec(points[0], points[1], max(0.0, min(1.0, u)))

    u = max(0.0, min(1.0, u))
    n_seg = n - 1
    pos = u * n_seg
    i = int(pos)
    if i >= n_seg:
        i = n_seg - 1
    t = pos - i

    p0 = points[i - 1] if i - 1 >= 0 else points[0]
    p1 = points[i]
    p2 = points[i + 1]
    p3 = points[i + 2] if i + 2 < n else points[-1]

    t2 = t * t
    t3 = t2 * t
    # Uniform Catmull-Rom basis.
    return (
        p1 * 2.0
        + (p2 - p0) * t
        + (p0 * 2.0 - p1 * 5.0 + p2 * 4.0 - p3) * t2
        + (p1 * 3.0 - p0 - p2 * 3.0 + p3) * t3
    ) * 0.5


def sample_catmull_rom(points: List[Vec3], count: int) -> List[Vec3]:
    """Sample ``count`` evenly-parameterised points along the spline."""
    if count <= 1 or len(points) <= 1:
        return [catmull_rom_at(points, 0.0)] if points else []
    return [catmull_rom_at(points, i / (count - 1)) for i in range(count)]
