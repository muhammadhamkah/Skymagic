"""Command-line interface for the Autonomous Drone Show Capture Planner.

Run ``python -m dronecam --help`` for the full list. Typical pre-production
flow::

    python -m dronecam make-sample  -o show.json
    python -m dronecam plan show.json -o path.json --mode orbit
    python -m dronecam simulate show.json path.json
    python -m dronecam export show.json path.json --lat 1.2897 --lon 103.851 -o out/

Or run the whole thing end-to-end on synthetic data::

    python -m dronecam demo -o out/
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .camera import CameraConfig, get_preset
from .export import export_mission
from .geometry import GeoOrigin
from .planner import AutoPlanOptions, CameraPath, plan_auto_path
from .samples import generate_sample_show
from .show import DroneShow
from .simulation import simulate


def _load_show(path: str) -> DroneShow:
    if path.lower().endswith(".csv"):
        return DroneShow.load_csv(path)
    return DroneShow.load_json(path)


def _load_camera(preset: str, config_path: Optional[str]) -> CameraConfig:
    if config_path:
        return CameraConfig.load_json(config_path)
    return get_preset(preset)


def _add_camera_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--preset", default="generic",
                   help="camera preset: generic | cine-mini | long-lens")
    p.add_argument("--camera-config", default=None,
                   help="path to a camera config JSON (overrides --preset)")


def cmd_make_sample(args) -> int:
    show = generate_sample_show(
        fps=args.fps, duration_s=args.duration,
        cols=args.cols, rows=args.rows,
    )
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(show.to_dict(), fh, indent=2)
    print(f"Wrote sample show with {show.num_drones} drones, "
          f"{show.num_frames} frames ({show.duration:.1f}s) -> {args.output}")
    return 0


def cmd_info(args) -> int:
    show = _load_show(args.show)
    mn, mx = show.bounds()
    print(f"Show         : {show.name}")
    print(f"Drones       : {show.num_drones}")
    print(f"Frames       : {show.num_frames} @ {show.fps:g} fps")
    print(f"Duration     : {show.duration:.2f} s")
    print(f"Bounds min   : ({mn.x:.1f}, {mn.y:.1f}, {mn.z:.1f}) m")
    print(f"Bounds max   : ({mx.x:.1f}, {mx.y:.1f}, {mx.z:.1f}) m")
    print(f"Max radius   : {show.max_radius():.2f} m")
    return 0


def cmd_plan(args) -> int:
    show = _load_show(args.show)
    camera = _load_camera(args.preset, args.camera_config)
    options = AutoPlanOptions(
        mode=args.mode,
        keyframe_count=args.keyframes,
        elevation_deg=args.elevation,
        orbit_degrees=args.orbit_degrees,
        start_azimuth_deg=args.azimuth,
        fill=args.fill,
    )
    path = plan_auto_path(show, camera, options)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(path.to_dict(camera), fh, indent=2)
    print(f"Planned '{path.name}' with {len(path.keyframes)} keyframes -> {args.output}")

    if not args.no_sim:
        result = simulate(show, camera, path)
        print("\n--- quick simulation ---")
        print(result.report())
    return 0


def cmd_simulate(args) -> int:
    show = _load_show(args.show)
    camera = _load_camera(args.preset, args.camera_config)
    path = CameraPath.from_dict(_load_json(args.path))
    result = simulate(show, camera, path, sample_fps=args.fps)
    print(result.report())
    if args.json:
        out = {
            "coverage_score": result.coverage_score,
            "fully_framed_fraction": result.fully_framed_fraction,
            "mean_visible": result.mean_visible,
            "min_visible": result.min_visible,
            "max_speed_mps": result.max_speed_mps,
            "violations": result.violation_summary,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nWrote simulation summary -> {args.json}")
    # non-zero exit if framing is poor, so it can gate CI/automation
    return 0 if result.mean_visible >= 0.95 else 2


def cmd_export(args) -> int:
    show = _load_show(args.show)
    camera = _load_camera(args.preset, args.camera_config)
    path = CameraPath.from_dict(_load_json(args.path))
    origin = GeoOrigin(
        latitude=args.lat, longitude=args.lon,
        altitude=args.alt, heading_deg=args.heading,
    )
    mission = export_mission(
        path, show, origin, camera, out_dir=args.output,
        basename=args.basename, sample_keyframes=args.max_waypoints,
    )
    print(f"Exported mission '{mission['show']}' "
          f"({mission['timeline']['waypoint_count']} waypoints):")
    for label, fpath in mission["files"].items():
        print(f"  {label:22s} {fpath}")
    return 0


def cmd_demo(args) -> int:
    print("=== Drone Show Capture Planner — end-to-end demo ===\n")
    show = generate_sample_show(duration_s=args.duration)
    camera = get_preset(args.preset)
    print(f"1. Imported show '{show.name}': {show.num_drones} drones, "
          f"{show.duration:.1f}s")

    options = AutoPlanOptions(mode=args.mode, keyframe_count=args.keyframes)
    path = plan_auto_path(show, camera, options)
    print(f"2. Planned camera path '{path.name}' "
          f"({len(path.keyframes)} keyframes) with '{camera.name}'")

    result = simulate(show, camera, path)
    print("3. Simulated capture:")
    for line in result.report().splitlines():
        print("     " + line)

    origin = GeoOrigin(latitude=args.lat, longitude=args.lon,
                       altitude=args.alt, heading_deg=args.heading)
    mission = export_mission(path, show, origin, camera, out_dir=args.output)
    print("4. Exported mission files:")
    for label, fpath in mission["files"].items():
        print(f"     {label:22s} {fpath}")
    print("\nDone. Open the .kml in Google Earth to inspect the flight path.")
    return 0


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dronecam",
        description="Autonomous Drone Show Capture Planner",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("make-sample", help="generate a synthetic drone show")
    p.add_argument("-o", "--output", default="show.json")
    p.add_argument("--fps", type=float, default=24.0)
    p.add_argument("--duration", type=float, default=8.0)
    p.add_argument("--cols", type=int, default=8)
    p.add_argument("--rows", type=int, default=5)
    p.set_defaults(func=cmd_make_sample)

    p = sub.add_parser("info", help="print stats about a show file")
    p.add_argument("show")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("plan", help="auto-generate a camera path for a show")
    p.add_argument("show")
    p.add_argument("-o", "--output", default="path.json")
    p.add_argument("--mode", default="orbit", choices=["orbit", "static", "flyby"])
    p.add_argument("--keyframes", type=int, default=24)
    p.add_argument("--elevation", type=float, default=18.0)
    p.add_argument("--orbit-degrees", type=float, default=90.0)
    p.add_argument("--azimuth", type=float, default=200.0)
    p.add_argument("--fill", type=float, default=0.72)
    p.add_argument("--no-sim", action="store_true", help="skip the quick simulation")
    _add_camera_args(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("simulate", help="simulate framing/limits for a path")
    p.add_argument("show")
    p.add_argument("path")
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--json", default=None, help="also write a JSON summary here")
    _add_camera_args(p)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("export", help="export a mission from a path")
    p.add_argument("show")
    p.add_argument("path")
    p.add_argument("-o", "--output", default="out", help="output directory")
    p.add_argument("--basename", default="mission")
    p.add_argument("--lat", type=float, required=True, help="venue origin latitude")
    p.add_argument("--lon", type=float, required=True, help="venue origin longitude")
    p.add_argument("--alt", type=float, default=0.0, help="venue origin altitude (m)")
    p.add_argument("--heading", type=float, default=0.0,
                   help="compass bearing of local +y axis (deg)")
    p.add_argument("--max-waypoints", type=int, default=None,
                   help="downsample to at most this many waypoints")
    _add_camera_args(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("demo", help="run the full pipeline on synthetic data")
    p.add_argument("-o", "--output", default="out")
    p.add_argument("--mode", default="orbit", choices=["orbit", "static", "flyby"])
    p.add_argument("--keyframes", type=int, default=24)
    p.add_argument("--duration", type=float, default=8.0)
    p.add_argument("--preset", default="generic")
    p.add_argument("--lat", type=float, default=1.2897)
    p.add_argument("--lon", type=float, default=103.8501)
    p.add_argument("--alt", type=float, default=0.0)
    p.add_argument("--heading", type=float, default=0.0)
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
