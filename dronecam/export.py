"""Export a planned camera path to executable mission files.

Three artefacts are produced from a :class:`CameraPath`, a :class:`DroneShow`
and a :class:`GeoOrigin`:

* a **generic mission JSON** – waypoints with GPS, altitude, yaw, gimbal pitch,
  zoom and recording actions plus an absolute timeline. This is the canonical
  output other systems can translate from.
* a **Litchi-style CSV** – importable by common waypoint apps for quick field
  use (latitude, longitude, altitude, heading, gimbal pitch, ...).
* a **KML** – the flight path for visual inspection in Google Earth / GIS.

All conversions go through :meth:`GeoOrigin.to_gps`, so the local metric world
frame is anchored to the real venue exactly once.
"""

from __future__ import annotations

import csv
import json
import math
from typing import List, Optional

from .camera import CameraConfig
from .geometry import GeoOrigin
from .planner import CameraPath
from .show import DroneShow


def _heading_deg_from_yaw(yaw_rad: float, origin: GeoOrigin) -> float:
    """Convert a world-frame yaw to a compass heading (deg, 0=N, CW).

    World yaw is measured CCW from East. Compass heading is CW from North.
    """
    bearing = 90.0 - math.degrees(yaw_rad) - origin.heading_deg
    return bearing % 360.0


def build_mission(
    path: CameraPath,
    show: DroneShow,
    origin: GeoOrigin,
    camera: CameraConfig,
    sample_keyframes: Optional[int] = None,
) -> dict:
    """Build the canonical mission dictionary from a resolved camera path.

    Each keyframe becomes a waypoint. Recording transitions are emitted as
    explicit ``start_recording`` / ``stop_recording`` actions so the flight
    controller knows when to roll.
    """
    kfs = path.keyframes
    if sample_keyframes and sample_keyframes < len(kfs) and sample_keyframes >= 2:
        idx = [round(i * (len(kfs) - 1) / (sample_keyframes - 1)) for i in range(sample_keyframes)]
        kfs = [kfs[i] for i in sorted(set(idx))]

    waypoints: List[dict] = []
    prev_recording = False
    t_start = path.start_time

    for i, kf in enumerate(kfs):
        # Resolve aim/zoom via the path so auto-aim keyframes are concrete.
        pose = path.pose_at(kf.t, camera, show)
        lat, lon, alt = origin.to_gps(pose.position)
        heading = _heading_deg_from_yaw(pose.yaw, origin)

        actions: List[str] = []
        if pose.recording and not prev_recording:
            actions.append("start_recording")
        elif not pose.recording and prev_recording:
            actions.append("stop_recording")
        prev_recording = pose.recording

        waypoints.append(
            {
                "index": i,
                "t": round(kf.t - t_start, 4),
                "latitude": round(lat, 8),
                "longitude": round(lon, 8),
                "altitude_m": round(alt, 3),
                "heading_deg": round(heading, 2),
                "gimbal_pitch_deg": round(math.degrees(pose.pitch), 2),
                "focal_mm": round(pose.focal_mm, 2),
                "zoom_ratio": round(pose.focal_mm / camera.focal_min_mm, 3),
                "recording": pose.recording,
                "actions": actions,
            }
        )

    if prev_recording:  # close the recording at the end of the flight
        waypoints[-1]["actions"].append("stop_recording")

    return {
        "format": "dronecam.mission",
        "version": 1,
        "show": show.name,
        "path": path.name,
        "camera": camera.name,
        "origin": {
            "latitude": origin.latitude,
            "longitude": origin.longitude,
            "altitude": origin.altitude,
            "heading_deg": origin.heading_deg,
        },
        "timeline": {
            "duration_s": round(path.end_time - path.start_time, 3),
            "waypoint_count": len(waypoints),
        },
        "waypoints": waypoints,
    }


def write_mission_json(mission: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(mission, fh, indent=2)


def write_litchi_csv(mission: dict, path: str) -> None:
    """Write a Litchi-compatible waypoint CSV (a widely understood subset)."""
    fields = [
        "latitude", "longitude", "altitude(m)", "heading(deg)",
        "curvesize(m)", "rotationdir", "gimbalmode", "gimbalpitchangle",
        "actiontype1", "actionparam1", "speed(m/s)",
    ]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for wp in mission["waypoints"]:
            action_type = 0
            action_param = 0
            if "start_recording" in wp["actions"]:
                action_type = 5   # Litchi: start recording
            elif "stop_recording" in wp["actions"]:
                action_type = 6   # Litchi: stop recording
            writer.writerow([
                wp["latitude"], wp["longitude"], wp["altitude_m"],
                wp["heading_deg"], 0.2, 0, 2, wp["gimbal_pitch_deg"],
                action_type, action_param, "",
            ])


def write_kml(mission: dict, path: str) -> None:
    """Write the flight path as a KML LineString for visual inspection."""
    coords = " ".join(
        f"{wp['longitude']},{wp['latitude']},{wp['altitude_m']}"
        for wp in mission["waypoints"]
    )
    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{mission['show']} - {mission['path']}</name>
    <Style id="flightpath">
      <LineStyle><color>ff00aaff</color><width>3</width></LineStyle>
    </Style>
    <Placemark>
      <name>Camera flight path</name>
      <styleUrl>#flightpath</styleUrl>
      <LineString>
        <altitudeMode>relativeToGround</altitudeMode>
        <coordinates>{coords}</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(kml)


def write_camera_timeline_csv(mission: dict, path: str) -> None:
    """Write the per-waypoint camera command timeline."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "t_s", "latitude", "longitude", "altitude_m", "heading_deg",
            "gimbal_pitch_deg", "focal_mm", "zoom_ratio", "recording", "actions",
        ])
        for wp in mission["waypoints"]:
            writer.writerow([
                wp["t"], wp["latitude"], wp["longitude"], wp["altitude_m"],
                wp["heading_deg"], wp["gimbal_pitch_deg"], wp["focal_mm"],
                wp["zoom_ratio"], int(wp["recording"]), "|".join(wp["actions"]),
            ])


def export_mission(
    path: CameraPath,
    show: DroneShow,
    origin: GeoOrigin,
    camera: CameraConfig,
    out_dir: str,
    basename: str = "mission",
    sample_keyframes: Optional[int] = None,
) -> dict:
    """Export all mission artefacts to ``out_dir`` and return the mission dict.

    Returns the canonical mission dictionary; the written file paths are listed
    under the ``"files"`` key.
    """
    import os

    os.makedirs(out_dir, exist_ok=True)
    mission = build_mission(path, show, origin, camera, sample_keyframes=sample_keyframes)

    json_path = os.path.join(out_dir, f"{basename}.json")
    litchi_path = os.path.join(out_dir, f"{basename}_litchi.csv")
    kml_path = os.path.join(out_dir, f"{basename}.kml")
    timeline_path = os.path.join(out_dir, f"{basename}_camera_timeline.csv")

    write_mission_json(mission, json_path)
    write_litchi_csv(mission, litchi_path)
    write_kml(mission, kml_path)
    write_camera_timeline_csv(mission, timeline_path)

    mission["files"] = {
        "mission_json": json_path,
        "litchi_csv": litchi_path,
        "kml": kml_path,
        "camera_timeline_csv": timeline_path,
    }
    return mission
