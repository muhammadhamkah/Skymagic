"""Drone show data model and importers.

A *show* is a time-sampled animation of N point lights (the drones). The
canonical interchange format is JSON exported from Blender (see
``tools/blender_export.py``), but a simple CSV form is also supported.

JSON schema (``frames`` form)::

    {
      "name": "Logo Reveal",
      "fps": 24,
      "frame_start": 1,
      "frame_end": 240,
      "up_axis": "Z",            # "Z" (Blender default) or "Y"
      "units": "meters",
      "drone_ids": ["d0", ...],  # optional
      "frames": [
        {"frame": 1, "points": [[x, y, z], ...]},
        ...
      ]
    }

After import all coordinates live in the dronecam world frame (x=East,
y=North, z=Up, metres). The ``up_axis`` field and an optional ``scale`` /
``offset`` let a Blender scene be mapped onto a real venue.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import Vec3, lerp_vec


@dataclass
class ShowFrame:
    """One animation frame: a timestamp and every drone's position."""

    t: float  # seconds from show start
    points: List[Vec3]

    def centroid(self) -> Vec3:
        if not self.points:
            return Vec3()
        acc = Vec3()
        for p in self.points:
            acc = acc + p
        return acc / len(self.points)


@dataclass
class DroneShow:
    """A time-sampled drone show in the dronecam world frame."""

    name: str
    fps: float
    frames: List[ShowFrame]
    drone_ids: Optional[List[str]] = None
    meta: Dict = field(default_factory=dict)

    # ----- basic stats -------------------------------------------------
    @property
    def num_drones(self) -> int:
        return max((len(f.points) for f in self.frames), default=0)

    @property
    def num_frames(self) -> int:
        return len(self.frames)

    @property
    def duration(self) -> float:
        if not self.frames:
            return 0.0
        return self.frames[-1].t - self.frames[0].t

    @property
    def start_time(self) -> float:
        return self.frames[0].t if self.frames else 0.0

    @property
    def end_time(self) -> float:
        return self.frames[-1].t if self.frames else 0.0

    # ----- sampling ----------------------------------------------------
    def points_at(self, t: float) -> List[Vec3]:
        """Drone positions at time ``t`` (seconds), linearly interpolated."""
        if not self.frames:
            return []
        if t <= self.frames[0].t:
            return list(self.frames[0].points)
        if t >= self.frames[-1].t:
            return list(self.frames[-1].points)
        # binary-ish linear scan (frame counts are modest)
        lo = self.frames[0]
        for hi in self.frames[1:]:
            if hi.t >= t:
                span = hi.t - lo.t
                u = 0.0 if span <= 0 else (t - lo.t) / span
                n = min(len(lo.points), len(hi.points))
                return [lerp_vec(lo.points[i], hi.points[i], u) for i in range(n)]
            lo = hi
        return list(self.frames[-1].points)

    def centroid_at(self, t: float) -> Vec3:
        pts = self.points_at(t)
        if not pts:
            return Vec3()
        acc = Vec3()
        for p in pts:
            acc = acc + p
        return acc / len(pts)

    def bounding_sphere_at(self, t: float) -> Tuple[Vec3, float]:
        """Centroid and radius enclosing all drones at time ``t``."""
        pts = self.points_at(t)
        if not pts:
            return Vec3(), 0.0
        c = self.centroid_at(t)
        r = max((p - c).length() for p in pts)
        return c, r

    def bounds(self) -> Tuple[Vec3, Vec3]:
        """Axis-aligned bounding box ``(min, max)`` over the whole show."""
        big = float("inf")
        mn = [big, big, big]
        mx = [-big, -big, -big]
        for fr in self.frames:
            for p in fr.points:
                mn[0] = min(mn[0], p.x); mx[0] = max(mx[0], p.x)
                mn[1] = min(mn[1], p.y); mx[1] = max(mx[1], p.y)
                mn[2] = min(mn[2], p.z); mx[2] = max(mx[2], p.z)
        if mn[0] == big:
            return Vec3(), Vec3()
        return Vec3(*mn), Vec3(*mx)

    def max_radius(self) -> float:
        """Largest bounding-sphere radius across the show (for standoff calc)."""
        return max((self.bounding_sphere_at(f.t)[1] for f in self.frames), default=0.0)

    # ----- import ------------------------------------------------------
    @classmethod
    def from_dict(
        cls,
        data: dict,
        scale: float = 1.0,
        offset: Vec3 = Vec3(),
    ) -> "DroneShow":
        fps = float(data.get("fps", 24))
        frame_start = int(data.get("frame_start", 1))
        up_axis = str(data.get("up_axis", "Z")).upper()

        raw_frames = data.get("frames")
        if raw_frames is None:
            raise ValueError("show data has no 'frames' array")

        def remap(p: Sequence[float]) -> Vec3:
            x, y, z = float(p[0]), float(p[1]), float(p[2])
            if up_axis == "Y":
                # Convert Y-up (e.g. some exporters) to our Z-up world frame.
                x, y, z = x, -z, y
            v = Vec3(x, y, z) * scale + offset
            return v

        frames: List[ShowFrame] = []
        for fr in raw_frames:
            frame_no = int(fr.get("frame", frame_start + len(frames)))
            if "t" in fr:
                t = float(fr["t"])
            else:
                t = (frame_no - frame_start) / fps if fps else 0.0
            points = [remap(p) for p in fr.get("points", [])]
            frames.append(ShowFrame(t=t, points=points))

        frames.sort(key=lambda f: f.t)
        return cls(
            name=str(data.get("name", "Untitled Show")),
            fps=fps,
            frames=frames,
            drone_ids=data.get("drone_ids"),
            meta={k: v for k, v in data.items() if k not in {"frames"}},
        )

    @classmethod
    def load_json(cls, path: str, **kwargs) -> "DroneShow":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh), **kwargs)

    @classmethod
    def load_csv(cls, path: str, fps: float = 24.0, **kwargs) -> "DroneShow":
        """Load a flat CSV with columns ``frame,drone,x,y,z``.

        Rows are grouped by frame; drones keep the order first seen.
        """
        by_frame: Dict[int, List[Tuple[str, Vec3]]] = {}
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                frame = int(float(row["frame"]))
                drone = str(row.get("drone", row.get("id", "")))
                v = Vec3(float(row["x"]), float(row["y"]), float(row["z"]))
                by_frame.setdefault(frame, []).append((drone, v))
        frames = []
        for frame in sorted(by_frame):
            entries = by_frame[frame]
            frames.append({"frame": frame, "points": [e[1].as_tuple() for e in entries]})
        data = {"name": "CSV Show", "fps": fps, "frames": frames}
        return cls.from_dict(data, **kwargs)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fps": self.fps,
            "up_axis": "Z",
            "units": "meters",
            "drone_ids": self.drone_ids,
            "frames": [
                {"frame": i, "t": fr.t, "points": [p.as_tuple() for p in fr.points]}
                for i, fr in enumerate(self.frames)
            ],
        }
