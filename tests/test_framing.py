import math
import unittest

from dronecam.camera import CameraConfig, CameraPose
from dronecam.framing import FramingEngine
from dronecam.geometry import Vec3, look_at_angles


class TestProjection(unittest.TestCase):
    def setUp(self):
        self.cam = CameraConfig()
        self.engine = FramingEngine(self.cam)

    def _pose_facing(self, eye, target):
        yaw, pitch = look_at_angles(eye, target)
        return CameraPose(position=eye, yaw=yaw, pitch=pitch, focal_mm=self.cam.focal_mm)

    def test_point_dead_center(self):
        pose = self._pose_facing(Vec3(0, 0, 0), Vec3(10, 0, 0))
        proj = self.engine.project(pose, Vec3(10, 0, 0))
        self.assertIsNotNone(proj)
        x, y, depth = proj
        self.assertAlmostEqual(x, 0.0, places=5)
        self.assertAlmostEqual(y, 0.0, places=5)
        self.assertAlmostEqual(depth, 10.0, places=5)

    def test_point_behind_returns_none(self):
        pose = self._pose_facing(Vec3(0, 0, 0), Vec3(10, 0, 0))
        self.assertIsNone(self.engine.project(pose, Vec3(-10, 0, 0)))

    def test_edge_of_frame(self):
        pose = self._pose_facing(Vec3(0, 0, 0), Vec3(10, 0, 0))
        # place a point at exactly the horizontal half-FOV angle, 10 m deep
        half = self.cam.hfov() / 2.0
        offset = 10.0 * math.tan(half)
        proj = self.engine.project(pose, Vec3(10, offset, 0))
        x, y, _ = proj
        self.assertAlmostEqual(abs(x), 1.0, places=4)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.cam = CameraConfig()
        self.engine = FramingEngine(self.cam)

    def test_all_visible_high_score(self):
        eye = Vec3(0, 0, 0)
        pts = [Vec3(20, 1, 1), Vec3(20, -1, -1), Vec3(20, 0, 0)]
        yaw, pitch = look_at_angles(eye, Vec3(20, 0, 0))
        pose = CameraPose(eye, yaw, pitch, self.cam.focal_mm)
        m = self.engine.evaluate(pose, pts, t=0.0)
        self.assertAlmostEqual(m.visible_fraction, 1.0)
        self.assertEqual(m.behind_fraction, 0.0)
        self.assertGreater(m.score, 0.5)

    def test_points_behind_lower_visibility(self):
        eye = Vec3(0, 0, 0)
        pts = [Vec3(20, 0, 0), Vec3(-20, 0, 0)]
        pose = CameraPose(eye, 0.0, 0.0, self.cam.focal_mm)
        m = self.engine.evaluate(pose, pts, t=0.0)
        self.assertAlmostEqual(m.behind_fraction, 0.5)
        self.assertAlmostEqual(m.visible_fraction, 0.5)
        self.assertTrue(any("behind" in w for w in m.warnings))

    def test_empty_points(self):
        pose = CameraPose(Vec3(), 0.0, 0.0, self.cam.focal_mm)
        m = self.engine.evaluate(pose, [], t=0.0)
        self.assertEqual(m.score, 0.0)


if __name__ == "__main__":
    unittest.main()
