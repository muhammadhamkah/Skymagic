"""Camera path representation and path-planning strategies.

A :class:`CameraPath` is a list of keyframes (time + position, with optional
look target / yaw / pitch / zoom). Sampling the path returns a fully resolved
:class:`~dronecam.camera.CameraPose` at any time, interpolating position
linearly, yaw along the shortest arc, and pitch/zoom linearly.

Three planning modes are provided:

* **manual** – build a path directly from user waypoints (this module just
  stores them; aiming can be explicit or "look at show centroid").
* **assisted** – take rough user waypoints and snap aim/zoom so the show stays
  framed (``refine_aim``).
* **automatic** – :func:`plan_auto_path` synthesises a complete orbiting or
  static path that keeps the whole show inside the frame with margin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .camera import CameraConfig, CameraPose
from .framing import FramingEngine
from .geometry import (
    Vec3,
    forward_vector,
    lerp,
    lerp_angle,
    lerp_vec,
    look_at_angles,
)
from .show import DroneShow


@dataclass
class PathKeyframe:
    """A single point on the camera path.

    If ``look_at_show`` is true, yaw/pitch are resolved at sample time to aim
    at the show centroid (great for "track the formation" shots). Otherwise the
    explicit ``yaw``/``pitch`` are used.
    """

    t: float
    position: Vec3
    yaw: Optional[float] = None        # radians; None -> resolved at sample
    pitch: Optional[float] = None      # radians
    focal_mm: Optional[float] = None   # None -> camera default
    recording: bool = True
    look_at_show: bool = True


@dataclass
class CameraPath:
    """An ordered set of keyframes defining the camera flight."""

    keyframes: List[PathKeyframe] = field(default_factory=list)
    name: str = "Camera Path"

    def __post_init__(self) -> None:
        self.keyframes.sort(key=lambda k: k.t)

    @property
    def start_time(self) -> float:
        return self.keyframes[0].t if self.keyframes else 0.0

    @property
    def end_time(self) -> float:
        return self.keyframes[-1].t if self.keyframes else 0.0

    def add(self, kf: PathKeyframe) -> None:
        self.keyframes.append(kf)
        self.keyframes.sort(key=lambda k: k.t)

    def _bracket(self, t: float):
        kfs = self.keyframes
        if t <= kfs[0].t:
            return kfs[0], kfs[0], 0.0
        if t >= kfs[-1].t:
            return kfs[-1], kfs[-1], 0.0
        lo = kfs[0]
        for hi in kfs[1:]:
            if hi.t >= t:
                span = hi.t - lo.t
                u = 0.0 if span <= 0 else (t - lo.t) / span
                return lo, hi, u
            lo = hi
        return kfs[-1], kfs[-1], 0.0

    def pose_at(
        self,
        t: float,
        camera: CameraConfig,
        show: Optional[DroneShow] = None,
    ) -> CameraPose:
        """Resolve a full :class:`CameraPose` at time ``t``."""
        if not self.keyframes:
            raise ValueError("camera path has no keyframes")
        lo, hi, u = self._bracket(t)
        position = lerp_vec(lo.position, hi.position, u)
        focal = lerp(
            lo.focal_mm if lo.focal_mm is not None else camera.focal_mm,
            hi.focal_mm if hi.focal_mm is not None else camera.focal_mm,
            u,
        )
        focal = camera.clamp_focal(focal)
        recording = lo.recording if u < 0.5 else hi.recording

        # Resolve aim. If either bracketing keyframe wants auto-aim and we have
        # a show, aim at the (interpolated) centroid; otherwise interpolate the
        # explicit angles.
        wants_auto = lo.look_at_show or hi.look_at_show
        if wants_auto and show is not None:
            target = show.centroid_at(t)
            yaw, pitch = look_at_angles(position, target)
        else:
            yaw_lo = lo.yaw if lo.yaw is not None else 0.0
            yaw_hi = hi.yaw if hi.yaw is not None else yaw_lo
            pitch_lo = lo.pitch if lo.pitch is not None else 0.0
            pitch_hi = hi.pitch if hi.pitch is not None else pitch_lo
            yaw = lerp_angle(yaw_lo, yaw_hi, u)
            pitch = lerp(pitch_lo, pitch_hi, u)

        pitch = camera.clamp_pitch(pitch)
        return CameraPose(position=position, yaw=yaw, pitch=pitch,
                          focal_mm=focal, recording=recording)

    def refine_aim(self, camera: CameraConfig, show: DroneShow) -> None:
        """Assisted mode: lock every keyframe's aim/zoom onto the show.

        Positions are kept as the user drew them; yaw/pitch are set to look at
        the show centroid and the focal length is chosen so the bounding sphere
        fills the frame within the safe margin.
        """
        for kf in self.keyframes:
            target = show.centroid_at(kf.t)
            yaw, pitch = look_at_angles(kf.position, target)
            kf.yaw = yaw
            kf.pitch = camera.clamp_pitch(pitch)
            kf.look_at_show = False
            _, radius = show.bounding_sphere_at(kf.t)
            dist = (target - kf.position).length()
            kf.focal_mm = _focal_to_fit(camera, radius, dist)

    def to_dict(self, camera: Optional[CameraConfig] = None) -> dict:
        return {
            "name": self.name,
            "keyframes": [
                {
                    "t": k.t,
                    "position": k.position.as_tuple(),
                    "yaw_deg": None if k.yaw is None else math.degrees(k.yaw),
                    "pitch_deg": None if k.pitch is None else math.degrees(k.pitch),
                    "focal_mm": k.focal_mm,
                    "recording": k.recording,
                    "look_at_show": k.look_at_show,
                }
                for k in self.keyframes
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CameraPath":
        kfs = []
        for k in data.get("keyframes", []):
            kfs.append(
                PathKeyframe(
                    t=float(k["t"]),
                    position=Vec3.from_iterable(k["position"]),
                    yaw=None if k.get("yaw_deg") is None else math.radians(k["yaw_deg"]),
                    pitch=None if k.get("pitch_deg") is None else math.radians(k["pitch_deg"]),
                    focal_mm=k.get("focal_mm"),
                    recording=bool(k.get("recording", True)),
                    look_at_show=bool(k.get("look_at_show", True)),
                )
            )
        return cls(keyframes=kfs, name=str(data.get("name", "Camera Path")))


def _focal_to_fit(camera: CameraConfig, radius: float, distance: float) -> float:
    """Pick a focal length so a sphere of ``radius`` at ``distance`` fits the frame.

    We want the sphere's angular radius to be ~ ``fill`` of the half-FOV. Using
    the narrower (vertical) axis guarantees it fits both ways.
    """
    if distance <= 1e-6 or radius <= 1e-6:
        return camera.focal_mm
    fill = 0.8  # leave headroom inside the (already inset) safe area
    desired_half_v = math.atan2(radius, distance) / fill
    desired_half_v = min(desired_half_v, math.radians(75.0))  # avoid extreme FOV
    if desired_half_v <= 1e-4:
        return camera.focal_max_mm
    focal = camera.sensor_height_mm / (2.0 * math.tan(desired_half_v))
    return camera.clamp_focal(focal)


def _standoff_distance(camera: CameraConfig, radius: float, focal_mm: float,
                       fill: float = 0.8) -> float:
    """Distance at which a sphere of ``radius`` fills ``fill`` of the frame."""
    half_fov = min(camera.hfov(focal_mm), camera.vfov(focal_mm)) / 2.0
    half_fov = max(half_fov, math.radians(1.0))
    return radius / math.tan(half_fov * fill)


def _max_centroid_speed(show: DroneShow) -> float:
    """Largest speed (m/s) of the show centroid between consecutive frames."""
    frames = show.frames
    fastest = 0.0
    for a, b in zip(frames, frames[1:]):
        dt = b.t - a.t
        if dt > 0:
            fastest = max(fastest, (b.centroid() - a.centroid()).length() / dt)
    return fastest


@dataclass
class AutoPlanOptions:
    """Tuning knobs for :func:`plan_auto_path`."""

    mode: str = "orbit"          # "orbit", "static", or "flyby"
    keyframe_count: int = 24     # number of keyframes synthesised
    elevation_deg: float = 18.0  # camera elevation above the show centroid
    orbit_degrees: float = 90.0  # total azimuth swept in "orbit" mode
    start_azimuth_deg: float = 200.0
    fill: float = 0.72           # target frame fill (0..1)
    min_standoff_m: float = 6.0  # never get closer than this
    min_altitude_m: float = 3.0  # keep the camera at least this high (AGL)
    margin: float = 0.12         # safe-area margin used by the framing engine


def plan_auto_path(
    show: DroneShow,
    camera: CameraConfig,
    options: Optional[AutoPlanOptions] = None,
) -> CameraPath:
    """Synthesise a complete camera path that keeps the show framed.

    The planner places the camera on a sphere around the show centroid at a
    standoff distance computed from the show's size and the lens, then either
    holds station ("static"), sweeps an arc ("orbit"), or flies a straight line
    past the show ("flyby"). Aim is locked to the centroid and the focal length
    is chosen per keyframe so the formation fills the frame.

    Speed limits are respected. The standoff is fixed (closest framing the
    *widest* lens allows, so the path stays near the show), and the camera's own
    motion budget is shared between the show's drift and the chosen move: the
    orbit sweep — or flyby travel — is capped so the camera never has to exceed
    ``camera.max_speed_mps``. Zoom then compensates to keep the framing tight.
    """
    opt = options or AutoPlanOptions()
    if show.num_frames == 0:
        return CameraPath(name=f"Auto ({opt.mode})")

    t0, t1 = show.start_time, show.end_time
    duration = max(t1 - t0, 1e-6)
    n = max(2, opt.keyframe_count)

    elev = math.radians(opt.elevation_deg)
    overall_radius = max(show.max_radius(), 0.5)

    # Standoff from the widest lens => the closest the camera can be while still
    # fitting the largest formation. Kept constant so there is no radial jitter;
    # per-keyframe zoom (below) fills the frame as the formation breathes.
    base_standoff = max(
        _standoff_distance(camera, overall_radius, camera.focal_min_mm, opt.fill),
        opt.min_standoff_m,
    )

    # The show centroid moves on its own; whatever speed budget is left after
    # following it is what we can spend orbiting / translating the camera.
    centroid_speed = _max_centroid_speed(show)
    move_budget = max(0.5, camera.max_speed_mps * 0.85 - centroid_speed)
    max_travel = move_budget * duration

    keyframes: List[PathKeyframe] = []
    start_az = math.radians(opt.start_azimuth_deg)

    # Cap the orbit sweep so the tangential speed stays within the budget.
    sweep = 0.0
    if opt.mode == "orbit":
        horiz_radius = max(base_standoff * math.cos(elev), 1e-3)
        max_sweep = max_travel / horiz_radius
        sweep = min(math.radians(opt.orbit_degrees), max_sweep)

    # For flyby we move the camera along a straight chord in front of the show,
    # capping the total travel to the budget.
    flyby_dir = Vec3(math.cos(start_az + math.pi / 2.0),
                     math.sin(start_az + math.pi / 2.0), 0.0)
    flyby_half = min(base_standoff, max_travel / 2.0)

    for i in range(n):
        u = i / (n - 1)
        t = t0 + u * duration
        centroid, radius = show.bounding_sphere_at(t)
        radius = max(radius, 0.5)
        standoff = base_standoff

        if opt.mode == "static":
            az = start_az
            direction = forward_vector(az, elev)
            position = centroid - direction * standoff
        elif opt.mode == "flyby":
            # Straight line passing the show, offset by standoff, slight rise.
            az = start_az
            base_dir = forward_vector(az, elev)
            base_pos = centroid - base_dir * standoff
            # travel along chord, centred at u=0.5
            travel = (u - 0.5) * 2.0 * flyby_half
            position = base_pos + flyby_dir * travel
        else:  # orbit
            az = start_az + sweep * u
            direction = forward_vector(az, elev)
            position = centroid - direction * standoff

        # Keep the camera above the ground floor; re-aim handles the new pose.
        if position.z < opt.min_altitude_m:
            position = Vec3(position.x, position.y, opt.min_altitude_m)

        target = centroid
        yaw, pitch = look_at_angles(position, target)
        pitch = camera.clamp_pitch(pitch)
        dist = (target - position).length()
        focal = _focal_to_fit(camera, radius, dist)

        keyframes.append(
            PathKeyframe(
                t=t,
                position=position,
                yaw=yaw,
                pitch=pitch,
                focal_mm=focal,
                recording=True,
                look_at_show=False,
            )
        )

    return CameraPath(keyframes=keyframes, name=f"Auto ({opt.mode})")
