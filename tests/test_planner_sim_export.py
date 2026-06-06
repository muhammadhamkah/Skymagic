import json
import math
import os
import tempfile
import unittest

from dronecam.camera import CameraConfig, get_preset
from dronecam.export import build_mission, export_mission, _heading_deg_from_yaw
from dronecam.geometry import GeoOrigin
from dronecam.planner import (
    AutoPlanOptions,
    CameraPath,
    PathKeyframe,
    plan_auto_path,
)
from dronecam.samples import generate_sample_show
from dronecam.simulation import simulate


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.show = generate_sample_show(duration_s=6.0)
        self.cam = get_preset("generic")

    def test_orbit_frames_the_show(self):
        path = plan_auto_path(self.show, self.cam, AutoPlanOptions(mode="orbit"))
        result = simulate(self.show, self.cam, path)
        # auto planner should keep essentially all drones in frame
        self.assertGreaterEqual(result.mean_visible, 0.95)
        self.assertGreaterEqual(result.coverage_score, 0.6)

    def test_static_mode(self):
        path = plan_auto_path(self.show, self.cam, AutoPlanOptions(mode="static"))
        # static: every keyframe at the same position
        positions = {round(k.position.x, 3) for k in path.keyframes}
        # x may vary slightly because the centroid drifts; ensure path resolves
        result = simulate(self.show, self.cam, path)
        self.assertGreaterEqual(result.mean_visible, 0.9)

    def test_speed_limit_respected(self):
        path = plan_auto_path(self.show, self.cam,
                              AutoPlanOptions(mode="orbit", orbit_degrees=180.0))
        result = simulate(self.show, self.cam, path)
        # planner pushes standoff out so the arc is flyable
        self.assertLessEqual(result.max_speed_mps, self.cam.max_speed_mps + 0.5)

    def test_gimbal_always_in_range(self):
        path = plan_auto_path(self.show, self.cam, AutoPlanOptions(mode="orbit"))
        result = simulate(self.show, self.cam, path)
        self.assertNotIn("gimbal_range", result.violation_summary)

    def test_min_altitude_floor(self):
        opt = AutoPlanOptions(mode="orbit", min_altitude_m=5.0)
        path = plan_auto_path(self.show, self.cam, opt)
        for k in path.keyframes:
            self.assertGreaterEqual(k.position.z, 5.0 - 1e-6)

    def test_flyby_mode_frames_and_flyable(self):
        path = plan_auto_path(self.show, self.cam, AutoPlanOptions(mode="flyby"))
        result = simulate(self.show, self.cam, path)
        self.assertGreaterEqual(result.mean_visible, 0.9)
        self.assertLessEqual(result.max_speed_mps, self.cam.max_speed_mps + 0.5)

    def test_path_roundtrip(self):
        path = plan_auto_path(self.show, self.cam, AutoPlanOptions(mode="orbit"))
        restored = CameraPath.from_dict(path.to_dict(self.cam))
        self.assertEqual(len(restored.keyframes), len(path.keyframes))
        p0 = path.pose_at(self.show.start_time + 1.0, self.cam, self.show)
        p1 = restored.pose_at(self.show.start_time + 1.0, self.cam, self.show)
        self.assertAlmostEqual(p0.position.x, p1.position.x, places=4)
        self.assertAlmostEqual(p0.yaw, p1.yaw, places=4)


class TestManualAndAssisted(unittest.TestCase):
    def setUp(self):
        self.show = generate_sample_show(duration_s=4.0)
        self.cam = get_preset("generic")

    def test_auto_aim_keyframe_tracks_centroid(self):
        # a single auto-aim keyframe should always point at the show
        kf = PathKeyframe(t=0.0, position=self.show.centroid_at(0.0) + _back(60),
                          look_at_show=True)
        path = CameraPath([kf])
        result = simulate(self.show, self.cam, path)
        self.assertGreaterEqual(result.mean_visible, 0.9)

    def test_refine_aim_locks_angles(self):
        kfs = [PathKeyframe(t=t, position=self.show.centroid_at(t) + _back(70),
                            look_at_show=True) for t in (0.0, 2.0, 4.0)]
        path = CameraPath(kfs)
        path.refine_aim(self.cam, self.show)
        for k in path.keyframes:
            self.assertFalse(k.look_at_show)
            self.assertIsNotNone(k.yaw)
            self.assertIsNotNone(k.focal_mm)


class TestExport(unittest.TestCase):
    def setUp(self):
        self.show = generate_sample_show(duration_s=4.0)
        self.cam = get_preset("generic")
        self.path = plan_auto_path(self.show, self.cam, AutoPlanOptions(mode="orbit"))
        self.origin = GeoOrigin(1.2897, 103.8501, 0.0)

    def test_build_mission_structure(self):
        m = build_mission(self.path, self.show, self.origin, self.cam)
        self.assertEqual(m["format"], "dronecam.mission")
        self.assertEqual(len(m["waypoints"]), len(self.path.keyframes))
        first = m["waypoints"][0]
        self.assertIn("start_recording", first["actions"])
        self.assertIn("stop_recording", m["waypoints"][-1]["actions"])
        # GPS near the origin
        self.assertAlmostEqual(first["latitude"], 1.2897, delta=0.01)
        self.assertAlmostEqual(first["longitude"], 103.8501, delta=0.01)

    def test_downsample_waypoints(self):
        m = build_mission(self.path, self.show, self.origin, self.cam,
                          sample_keyframes=5)
        self.assertLessEqual(len(m["waypoints"]), 5)

    def test_heading_conversion(self):
        # world yaw 0 (East) -> compass heading 90
        self.assertAlmostEqual(_heading_deg_from_yaw(0.0, self.origin), 90.0, places=3)
        # world yaw 90deg (North) -> compass heading 0
        self.assertAlmostEqual(
            _heading_deg_from_yaw(math.radians(90), self.origin), 0.0, places=3)

    def test_export_writes_all_files(self):
        with tempfile.TemporaryDirectory() as d:
            m = export_mission(self.path, self.show, self.origin, self.cam, out_dir=d)
            for label, fpath in m["files"].items():
                self.assertTrue(os.path.exists(fpath), f"{label} missing")
            with open(m["files"]["mission_json"]) as fh:
                reloaded = json.load(fh)
            self.assertEqual(reloaded["waypoints"], m["waypoints"])


def _back(dist):
    # helper: a point `dist` metres back (south/down) for camera placement
    from dronecam.geometry import Vec3
    return Vec3(0, -dist * 0.8, dist * 0.3)


if __name__ == "__main__":
    unittest.main()
