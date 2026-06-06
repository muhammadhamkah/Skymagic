"""Camera drone configuration and instantaneous camera pose."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from .geometry import Vec3


@dataclass
class CameraConfig:
    """Describes a camera drone: optics, gimbal and flight envelope.

    Field of view is derived from the active focal length and the sensor
    size. Zoom is modelled as a focal-length range; ``focal_mm`` is the
    currently selected value and is clamped to ``[focal_min_mm, focal_max_mm]``.
    """

    name: str = "Generic Cinewhoop"
    # Optics
    sensor_width_mm: float = 13.2
    sensor_height_mm: float = 8.8
    focal_min_mm: float = 8.8
    focal_max_mm: float = 24.0
    focal_mm: float = 8.8
    # Gimbal limits (degrees). Pitch: negative looks down.
    gimbal_pitch_min_deg: float = -90.0
    gimbal_pitch_max_deg: float = 30.0
    # Flight envelope
    max_speed_mps: float = 12.0
    max_ascent_mps: float = 6.0
    max_descent_mps: float = 4.0
    max_yaw_rate_dps: float = 90.0
    max_gimbal_rate_dps: float = 90.0
    # Recording
    record_width: int = 3840
    record_height: int = 2160
    record_fps: int = 30

    # ----- derived optics ---------------------------------------------
    @property
    def aspect(self) -> float:
        return self.sensor_width_mm / self.sensor_height_mm

    def clamp_focal(self, focal_mm: float) -> float:
        return max(self.focal_min_mm, min(self.focal_max_mm, focal_mm))

    def hfov(self, focal_mm: float | None = None) -> float:
        """Horizontal field of view in radians."""
        f = self.clamp_focal(self.focal_mm if focal_mm is None else focal_mm)
        return 2.0 * math.atan(self.sensor_width_mm / (2.0 * f))

    def vfov(self, focal_mm: float | None = None) -> float:
        """Vertical field of view in radians."""
        f = self.clamp_focal(self.focal_mm if focal_mm is None else focal_mm)
        return 2.0 * math.atan(self.sensor_height_mm / (2.0 * f))

    def clamp_pitch(self, pitch_rad: float) -> float:
        lo = math.radians(self.gimbal_pitch_min_deg)
        hi = math.radians(self.gimbal_pitch_max_deg)
        return max(lo, min(hi, pitch_rad))

    def pitch_in_range(self, pitch_rad: float) -> bool:
        lo = math.radians(self.gimbal_pitch_min_deg)
        hi = math.radians(self.gimbal_pitch_max_deg)
        return lo - 1e-6 <= pitch_rad <= hi + 1e-6

    # ----- I/O ---------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CameraConfig":
        fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in fields})

    @classmethod
    def load_json(cls, path: str) -> "CameraConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)


@dataclass
class CameraPose:
    """An instantaneous camera state along the flight path."""

    position: Vec3
    yaw: float    # radians
    pitch: float  # radians (gimbal tilt)
    focal_mm: float
    recording: bool = True


# A couple of ready-made presets so users have a starting point.
PRESETS = {
    "generic": CameraConfig(),
    "cine-mini": CameraConfig(
        name="Cinema Mini 4K",
        sensor_width_mm=13.2,
        sensor_height_mm=8.8,
        focal_min_mm=8.8,
        focal_max_mm=8.8,
        focal_mm=8.8,
        gimbal_pitch_min_deg=-90.0,
        gimbal_pitch_max_deg=20.0,
        max_speed_mps=15.0,
        max_yaw_rate_dps=120.0,
    ),
    "long-lens": CameraConfig(
        name="Heavy Lift + Zoom",
        sensor_width_mm=23.5,
        sensor_height_mm=15.6,
        focal_min_mm=18.0,
        focal_max_mm=70.0,
        focal_mm=24.0,
        gimbal_pitch_min_deg=-100.0,
        gimbal_pitch_max_deg=45.0,
        max_speed_mps=8.0,
        max_yaw_rate_dps=60.0,
        max_gimbal_rate_dps=45.0,
    ),
}


def get_preset(name: str) -> CameraConfig:
    try:
        # return a copy so callers can mutate freely
        return CameraConfig.from_dict(PRESETS[name].to_dict())
    except KeyError as exc:
        raise KeyError(
            f"unknown preset '{name}'; choose from {sorted(PRESETS)}"
        ) from exc
