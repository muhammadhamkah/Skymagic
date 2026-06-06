"""Synthetic drone-show generators for demos, examples and tests.

These stand in for a real Blender export so the whole pipeline can be run with
zero external assets. :func:`generate_sample_show` builds a formation of drones
that drifts and rotates over time, giving the planner a moving, non-trivial
target to frame.
"""

from __future__ import annotations

import math
from typing import List

from .geometry import Vec3
from .show import DroneShow, ShowFrame


def _grid_formation(cols: int, rows: int, spacing: float) -> List[Vec3]:
    """A centred planar grid of points in the XZ (facing) plane."""
    pts: List[Vec3] = []
    x0 = -(cols - 1) * spacing / 2.0
    z0 = -(rows - 1) * spacing / 2.0
    for r in range(rows):
        for c in range(cols):
            pts.append(Vec3(x0 + c * spacing, 0.0, z0 + r * spacing))
    return pts


def generate_sample_show(
    name: str = "Sample Formation",
    fps: float = 24.0,
    duration_s: float = 8.0,
    cols: int = 8,
    rows: int = 5,
    spacing: float = 4.0,
    base_height: float = 40.0,
    rotate_deg: float = 60.0,
) -> DroneShow:
    """Build a rotating, rising grid formation centred above the origin.

    The formation starts low and slightly behind the origin, rises to
    ``base_height`` and yaws by ``rotate_deg`` over the show — enough motion to
    exercise the framing engine and the orbit planner.
    """
    base = _grid_formation(cols, rows, spacing)
    n_frames = max(2, int(round(duration_s * fps)))
    frames: List[ShowFrame] = []

    for i in range(n_frames):
        t = i / fps
        u = i / (n_frames - 1)
        yaw = math.radians(rotate_deg) * u
        cz, sz = math.cos(yaw), math.sin(yaw)
        # rise and a gentle lateral drift
        center = Vec3(
            6.0 * math.sin(u * math.pi),       # drift east/back
            10.0 * u,                          # drift north
            base_height * (0.4 + 0.6 * u),     # rise
        )
        # a soft breathing scale so the bounding sphere changes size
        scale = 1.0 + 0.15 * math.sin(u * 2.0 * math.pi)
        pts: List[Vec3] = []
        for p in base:
            rx = (p.x * cz - p.y * sz) * scale
            ry = (p.x * sz + p.y * cz) * scale
            pts.append(Vec3(rx, ry, p.z * scale) + center)
        frames.append(ShowFrame(t=t, points=pts))

    return DroneShow(name=name, fps=fps, frames=frames,
                     drone_ids=[f"d{i}" for i in range(len(base))])
