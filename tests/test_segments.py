import unittest

from dronecam.camera import get_preset
from dronecam.geometry import Vec3
from dronecam.samples import generate_sample_show
from dronecam.segments import (
    SegmentPlanOptions,
    ShotSegment,
    plan_segments,
)
from dronecam.simulation import simulate
from dronecam.spline import catmull_rom_at, sample_catmull_rom, smoothstep


class TestSpline(unittest.TestCase):
    def test_degenerate_cases(self):
        self.assertEqual(catmull_rom_at([], 0.5).as_tuple(), (0, 0, 0))
        self.assertEqual(catmull_rom_at([Vec3(1, 2, 3)], 0.5).as_tuple(), (1, 2, 3))
        mid = catmull_rom_at([Vec3(0, 0, 0), Vec3(10, 0, 0)], 0.5)
        self.assertAlmostEqual(mid.x, 5.0)

    def test_passes_through_control_points(self):
        pts = [Vec3(0, 0, 0), Vec3(10, 5, 0), Vec3(20, 0, 5), Vec3(30, -5, 0)]
        start = catmull_rom_at(pts, 0.0)
        end = catmull_rom_at(pts, 1.0)
        self.assertAlmostEqual(start.x, 0.0, places=5)
        self.assertAlmostEqual(end.x, 30.0, places=5)
        # a sampled curve hits the right count and stays finite
        sampled = sample_catmull_rom(pts, 20)
        self.assertEqual(len(sampled), 20)

    def test_smoothstep_bounds(self):
        self.assertEqual(smoothstep(0.0), 0.0)
        self.assertEqual(smoothstep(1.0), 1.0)
        self.assertAlmostEqual(smoothstep(0.5), 0.5)
        self.assertEqual(smoothstep(-1.0), 0.0)  # clamps


class TestPlanSegments(unittest.TestCase):
    def setUp(self):
        self.show = generate_sample_show(duration_s=8.0)
        self.cam = get_preset("generic")

    def _vantage(self, t, back=70.0):
        c = self.show.centroid_at(t)
        return Vec3(c.x, c.y - back * 0.8, max(5.0, c.z - back * 0.2))

    def test_single_static_scene_frames_show(self):
        seg = ShotSegment("s1", 0.0, 8.0, [self._vantage(4.0)])
        path = plan_segments(self.show, self.cam, [seg])
        self.assertGreater(len(path.keyframes), 2)
        result = simulate(self.show, self.cam, path)
        self.assertGreaterEqual(result.mean_visible, 0.9)

    def test_drawn_path_smooths_through_waypoints(self):
        wps = [self._vantage(0.0), self._vantage(2.0) + Vec3(20, 0, 0),
               self._vantage(4.0)]
        seg = ShotSegment("draw", 0.0, 4.0, wps)
        path = plan_segments(self.show, self.cam, [seg])
        # baked keyframes span the scene
        self.assertAlmostEqual(path.start_time, 0.0, places=3)
        self.assertAlmostEqual(path.end_time, 4.0, places=3)

    def test_two_scenes_get_a_transition(self):
        s1 = ShotSegment("s1", 0.0, 3.0, [self._vantage(1.5)])
        s2 = ShotSegment("s2", 5.0, 8.0, [self._vantage(6.5) + Vec3(40, 0, 0)])
        path = plan_segments(self.show, self.cam, [s1, s2])
        # there should be keyframes in the 3..5s transition gap
        gap = [k for k in path.keyframes if 3.0 < k.t < 5.0]
        self.assertTrue(gap, "expected transition keyframes in the scene gap")
        # times are strictly increasing and de-duplicated
        ts = [k.t for k in path.keyframes]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(len(ts), len(set(round(t, 6) for t in ts)))

    def test_transition_recording_toggle(self):
        s1 = ShotSegment("s1", 0.0, 3.0, [self._vantage(1.5)])
        s2 = ShotSegment("s2", 5.0, 8.0, [self._vantage(6.5)])
        opt = SegmentPlanOptions(record_transitions=False)
        path = plan_segments(self.show, self.cam, [s1, s2], opt)
        gap = [k for k in path.keyframes if 3.0 < k.t < 5.0]
        self.assertTrue(all(not k.recording for k in gap))

    def test_empty_segments(self):
        self.assertEqual(len(plan_segments(self.show, self.cam, []).keyframes), 0)
        # a segment with no waypoints is skipped
        seg = ShotSegment("nope", 0.0, 4.0, [])
        self.assertEqual(len(plan_segments(self.show, self.cam, [seg]).keyframes), 0)


if __name__ == "__main__":
    unittest.main()
