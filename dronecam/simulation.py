"""Simulate a planned capture: step the timeline, score framing, check limits.

:func:`simulate` walks the show timeline at a chosen sample rate, resolves the
camera pose from the path, evaluates framing with :class:`FramingEngine`, and
verifies that the motion respects the drone's flight envelope (speed, climb
rate, yaw rate, gimbal rate/range, zoom range). The result is a coverage report
the user can approve before exporting a real mission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

from .camera import CameraConfig
from .framing import FrameMetrics, FramingEngine
from .geometry import angle_diff
from .planner import CameraPath
from .show import DroneShow


@dataclass
class SampleRecord:
    """Everything computed for a single simulation sample."""

    t: float
    metrics: FrameMetrics
    speed_mps: float
    climb_mps: float
    yaw_rate_dps: float
    gimbal_rate_dps: float
    violations: List[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    """Aggregate report over a simulated capture."""

    samples: List[SampleRecord]
    coverage_score: float          # mean framing score, 0..1
    fully_framed_fraction: float   # fraction of samples with all drones visible
    mean_visible: float
    min_visible: float
    max_speed_mps: float
    max_climb_mps: float
    max_yaw_rate_dps: float
    max_gimbal_rate_dps: float
    violation_count: int
    violation_summary: dict = field(default_factory=dict)

    def report(self) -> str:
        """Human-readable multi-line summary."""
        lines = [
            f"Coverage score      : {self.coverage_score:6.1%}",
            f"Fully framed frames : {self.fully_framed_fraction:6.1%}",
            f"Visible drones      : mean {self.mean_visible:5.1%}  min {self.min_visible:5.1%}",
            f"Max speed           : {self.max_speed_mps:6.2f} m/s",
            f"Max climb/descent   : {self.max_climb_mps:6.2f} m/s",
            f"Max yaw rate        : {self.max_yaw_rate_dps:6.1f} deg/s",
            f"Max gimbal rate     : {self.max_gimbal_rate_dps:6.1f} deg/s",
            f"Constraint warnings : {self.violation_count}",
        ]
        if self.violation_summary:
            lines.append("  " + ", ".join(
                f"{k}: {v}" for k, v in sorted(self.violation_summary.items())
            ))
        return "\n".join(lines)


def simulate(
    show: DroneShow,
    camera: CameraConfig,
    path: CameraPath,
    sample_fps: float | None = None,
    safe_margin: float = 0.12,
) -> SimulationResult:
    """Run the capture simulation and return a :class:`SimulationResult`."""
    engine = FramingEngine(camera, safe_margin=safe_margin)
    fps = sample_fps or show.fps or 24.0
    dt = 1.0 / fps

    t0 = show.start_time
    t1 = show.end_time
    n_steps = max(1, int(round((t1 - t0) * fps)))

    samples: List[SampleRecord] = []
    prev_pose = None
    prev_t = None

    for i in range(n_steps + 1):
        t = min(t1, t0 + i * dt)
        pose = path.pose_at(t, camera, show)
        points = show.points_at(t)
        metrics = engine.evaluate(pose, points, t)

        speed = climb = yaw_rate = gimbal_rate = 0.0
        violations: List[str] = []
        if prev_pose is not None and prev_t is not None:
            step_dt = max(t - prev_t, 1e-6)
            delta = pose.position - prev_pose.position
            speed = delta.length() / step_dt
            climb = delta.z / step_dt
            yaw_rate = abs(math.degrees(angle_diff(pose.yaw, prev_pose.yaw))) / step_dt
            gimbal_rate = abs(math.degrees(pose.pitch - prev_pose.pitch)) / step_dt

            if speed > camera.max_speed_mps + 1e-6:
                violations.append("speed")
            if climb > camera.max_ascent_mps + 1e-6:
                violations.append("ascent")
            if -climb > camera.max_descent_mps + 1e-6:
                violations.append("descent")
            if yaw_rate > camera.max_yaw_rate_dps + 1e-6:
                violations.append("yaw_rate")
            if gimbal_rate > camera.max_gimbal_rate_dps + 1e-6:
                violations.append("gimbal_rate")
        if not camera.pitch_in_range(pose.pitch):
            violations.append("gimbal_range")

        samples.append(
            SampleRecord(
                t=t,
                metrics=metrics,
                speed_mps=speed,
                climb_mps=climb,
                yaw_rate_dps=yaw_rate,
                gimbal_rate_dps=gimbal_rate,
                violations=violations,
            )
        )
        prev_pose = pose
        prev_t = t

    return _aggregate(samples)


def _aggregate(samples: List[SampleRecord]) -> SimulationResult:
    if not samples:
        return SimulationResult([], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, {})

    n = len(samples)
    coverage = sum(s.metrics.score for s in samples) / n
    fully = sum(1 for s in samples if s.metrics.visible_fraction >= 0.999) / n
    visibles = [s.metrics.visible_fraction for s in samples]
    mean_visible = sum(visibles) / n
    min_visible = min(visibles)

    violation_summary: dict = {}
    violation_count = 0
    for s in samples:
        for v in s.violations:
            violation_summary[v] = violation_summary.get(v, 0) + 1
            violation_count += 1

    return SimulationResult(
        samples=samples,
        coverage_score=coverage,
        fully_framed_fraction=fully,
        mean_visible=mean_visible,
        min_visible=min_visible,
        max_speed_mps=max(s.speed_mps for s in samples),
        max_climb_mps=max(abs(s.climb_mps) for s in samples),
        max_yaw_rate_dps=max(s.yaw_rate_dps for s in samples),
        max_gimbal_rate_dps=max(s.gimbal_rate_dps for s in samples),
        violation_count=violation_count,
        violation_summary=violation_summary,
    )
