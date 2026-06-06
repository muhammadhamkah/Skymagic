"""Framing engine: project the show into the camera frame and score it.

The engine answers, for any camera pose at any instant: how much of the show
is inside the frame, how well-centred is it, and how much of the frame does it
fill? These per-frame metrics drive both the automatic planner and the
simulation's coverage report.

Normalised image coordinates run from ``-1`` (left/bottom edge) to ``+1``
(right/top edge). The ``safe_margin`` keeps the action away from the very edge
of the sensor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .camera import CameraConfig, CameraPose
from .geometry import Vec3, camera_basis


@dataclass
class FrameMetrics:
    """Framing quality for a single instant."""

    t: float
    visible_fraction: float       # drones in front of camera AND within frame
    in_safe_fraction: float       # drones within the safe (margin-inset) frame
    fill: float                   # 0..1, how much of the safe area the show spans
    center_error: float           # 0..~1.4, distance of show centre from image centre
    mean_depth: float             # average distance to visible drones (m)
    behind_fraction: float        # drones behind the camera
    score: float = 0.0            # composite 0..1 (higher is better)
    warnings: List[str] = field(default_factory=list)


class FramingEngine:
    """Projects show points and scores framing for a camera configuration."""

    def __init__(self, camera: CameraConfig, safe_margin: float = 0.12):
        self.camera = camera
        self.safe_margin = safe_margin

    def project(
        self, pose: CameraPose, point: Vec3
    ) -> Optional[Tuple[float, float, float]]:
        """Project a world point to normalised image coords.

        Returns ``(ndc_x, ndc_y, depth)`` or ``None`` if the point is behind
        the camera. ``depth`` is the distance along the forward axis (metres).
        """
        forward, right, up = camera_basis(pose.yaw, pose.pitch)
        rel = point - pose.position
        depth = rel.dot(forward)
        if depth <= 1e-6:
            return None
        tan_h = math.tan(self.camera.hfov(pose.focal_mm) / 2.0)
        tan_v = math.tan(self.camera.vfov(pose.focal_mm) / 2.0)
        ndc_x = (rel.dot(right) / depth) / tan_h
        ndc_y = (rel.dot(up) / depth) / tan_v
        return ndc_x, ndc_y, depth

    def evaluate(self, pose: CameraPose, points: List[Vec3], t: float) -> FrameMetrics:
        """Compute :class:`FrameMetrics` for a pose and a set of drone points."""
        if not points:
            return FrameMetrics(t, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, score=0.0,
                                warnings=["no drones at this time"])

        n = len(points)
        in_frame = 0
        in_safe = 0
        behind = 0
        depths: List[float] = []
        xs_in: List[float] = []
        ys_in: List[float] = []
        safe = 1.0 - self.safe_margin

        sum_x = sum_y = 0.0
        for p in points:
            proj = self.project(pose, p)
            if proj is None:
                behind += 1
                continue
            x, y, depth = proj
            depths.append(depth)
            if abs(x) <= 1.0 and abs(y) <= 1.0:
                in_frame += 1
                xs_in.append(x)
                ys_in.append(y)
            if abs(x) <= safe and abs(y) <= safe:
                in_safe += 1
            sum_x += x
            sum_y += y

        front = n - behind
        visible_fraction = in_frame / n
        in_safe_fraction = in_safe / n
        behind_fraction = behind / n
        mean_depth = sum(depths) / len(depths) if depths else 0.0

        # Centre error uses the mean projected position of *front* drones.
        if front > 0:
            cx = sum_x / front
            cy = sum_y / front
            center_error = math.hypot(cx, cy)
        else:
            center_error = math.sqrt(2.0)

        # Fill: span of the in-frame drones relative to the safe area.
        if xs_in and ys_in:
            span_x = (max(xs_in) - min(xs_in)) / (2.0 * safe)
            span_y = (max(ys_in) - min(ys_in)) / (2.0 * safe)
            fill = max(span_x, span_y)
            fill = max(0.0, min(1.0, fill))
        else:
            fill = 0.0

        warnings: List[str] = []
        if behind_fraction > 0.0:
            warnings.append(f"{behind} drone(s) behind camera")
        if visible_fraction < 0.999:
            warnings.append(f"{n - in_frame} drone(s) outside frame")
        if in_safe_fraction < visible_fraction:
            warnings.append("show touching frame edge (inside safe margin)")

        score = self._score(visible_fraction, in_safe_fraction, fill, center_error)
        return FrameMetrics(
            t=t,
            visible_fraction=visible_fraction,
            in_safe_fraction=in_safe_fraction,
            fill=fill,
            center_error=center_error,
            mean_depth=mean_depth,
            behind_fraction=behind_fraction,
            score=score,
            warnings=warnings,
        )

    def _score(
        self, visible: float, in_safe: float, fill: float, center_error: float
    ) -> float:
        """Blend the framing metrics into a single 0..1 quality score.

        Visibility dominates (you must see the show), then keeping it within the
        safe margin, then good use of the frame (fill), with a penalty for being
        off-centre. The ``fill`` target is ~0.7: filling too little wastes the
        frame, filling beyond the safe area is already penalised by ``in_safe``.
        """
        centering = max(0.0, 1.0 - center_error / math.sqrt(2.0))
        fill_quality = 1.0 - abs(fill - 0.7) / 0.7
        fill_quality = max(0.0, min(1.0, fill_quality))
        return (
            0.45 * visible
            + 0.25 * in_safe
            + 0.18 * centering
            + 0.12 * fill_quality
        )
