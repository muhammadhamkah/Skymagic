"""Scene-by-scene ("shot segment") camera planning with auto transitions.

A real show is filmed scene by scene: a deliberate vantage for each formation,
and a reposition during the transition beats so the drone is ready for the next
scene. A :class:`ShotSegment` captures one scene — its frame range (as seconds)
and the rough camera waypoints the user dropped. :func:`plan_segments` turns a
list of them into a single smooth :class:`~dronecam.planner.CameraPath`:

* within a scene, the camera glides through the waypoints on a Catmull-Rom
  spline (a single waypoint = a fixed vantage), always aiming at the drone-cloud
  centroid, with the focal length chosen to fill the frame;
* between scenes, the gap frames become an eased repositioning move from where
  one scene ended to where the next begins, while still watching the show.

Everything is *baked* into dense keyframes (sampled at ``bake_hz``) so the
planner, the simulation and the linear-interpolated Blender preview camera all
agree exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .camera import CameraConfig
from .geometry import Vec3, lerp_vec, look_at_angles
from .planner import CameraPath, PathKeyframe, _focal_to_fit
from .show import DroneShow
from .spline import catmull_rom_at, smoothstep


@dataclass
class ShotSegment:
    """One scene's shot: a time span and the rough camera waypoints."""

    name: str
    t_start: float
    t_end: float
    waypoints: List[Vec3]
    recording: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.t_end - self.t_start)


@dataclass
class SegmentPlanOptions:
    bake_hz: float = 2.0            # keyframes baked per second
    fill: float = 0.72             # target frame fill for zoom
    record_transitions: bool = True  # keep rolling while repositioning


def _bake_times(t0: float, t1: float, hz: float) -> List[float]:
    """Even sample times across ``[t0, t1]`` including both ends."""
    span = max(0.0, t1 - t0)
    n = max(2, int(round(span * max(hz, 0.1))) + 1)
    return [t0 + (t1 - t0) * (i / (n - 1)) for i in range(n)]


def _keyframe_at(
    show: DroneShow,
    camera: CameraConfig,
    t: float,
    position: Vec3,
    recording: bool,
    fill: float,
) -> PathKeyframe:
    """Build a fully-resolved keyframe aiming at the cloud centroid."""
    centroid, radius = show.bounding_sphere_at(t)
    yaw, pitch = look_at_angles(position, centroid)
    pitch = camera.clamp_pitch(pitch)
    dist = (centroid - position).length()
    focal = _focal_to_fit(camera, max(radius, 0.5), dist)
    return PathKeyframe(
        t=t,
        position=position,
        yaw=yaw,
        pitch=pitch,
        focal_mm=focal,
        recording=recording,
        look_at_show=False,
    )


def plan_segments(
    show: DroneShow,
    camera: CameraConfig,
    segments: List[ShotSegment],
    options: Optional[SegmentPlanOptions] = None,
) -> CameraPath:
    """Plan a smooth multi-scene camera path with auto transitions."""
    opt = options or SegmentPlanOptions()
    segs = [s for s in sorted(segments, key=lambda s: s.t_start) if s.waypoints]
    if not segs:
        return CameraPath(name="Segments (empty)")

    keyframes: List[PathKeyframe] = []

    def add(t, pos, rec):
        keyframes.append(_keyframe_at(show, camera, t, pos, rec, opt.fill))

    for idx, seg in enumerate(segs):
        # --- the scene itself: glide through the waypoints ---
        times = _bake_times(seg.t_start, seg.t_end, opt.bake_hz)
        for ti in times:
            u = 0.0 if seg.duration <= 0 else (ti - seg.t_start) / seg.duration
            pos = catmull_rom_at(seg.waypoints, u)
            add(ti, pos, seg.recording)

        # --- transition to the next scene (eased reposition) ---
        if idx + 1 < len(segs):
            nxt = segs[idx + 1]
            gap0, gap1 = seg.t_end, nxt.t_start
            if gap1 > gap0:
                start_pos = catmull_rom_at(seg.waypoints, 1.0)
                end_pos = catmull_rom_at(nxt.waypoints, 0.0)
                # interior points only; the scene endpoints already exist
                trans_times = _bake_times(gap0, gap1, opt.bake_hz)[1:-1]
                for ti in trans_times:
                    s = smoothstep((ti - gap0) / (gap1 - gap0))
                    pos = lerp_vec(start_pos, end_pos, s)
                    add(ti, pos, opt.record_transitions)

    # Deduplicate identical timestamps that can appear where a scene end meets a
    # transition start, keeping the first.
    deduped: List[PathKeyframe] = []
    seen = set()
    for kf in sorted(keyframes, key=lambda k: k.t):
        key = round(kf.t, 6)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(kf)

    return CameraPath(keyframes=deduped, name=f"Segments ({len(segs)} scenes)")
