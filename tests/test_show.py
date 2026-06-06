import unittest

from dronecam.geometry import Vec3
from dronecam.samples import generate_sample_show
from dronecam.show import DroneShow


class TestShowImport(unittest.TestCase):
    def test_from_dict_basic(self):
        data = {
            "name": "T",
            "fps": 10,
            "frame_start": 0,
            "frames": [
                {"frame": 0, "points": [[0, 0, 0], [2, 0, 0]]},
                {"frame": 10, "points": [[0, 0, 10], [2, 0, 10]]},
            ],
        }
        show = DroneShow.from_dict(data)
        self.assertEqual(show.num_drones, 2)
        self.assertEqual(show.num_frames, 2)
        self.assertAlmostEqual(show.duration, 1.0)  # 10 frames @ 10 fps

    def test_y_up_remap(self):
        data = {
            "fps": 1, "frame_start": 0, "up_axis": "Y",
            "frames": [{"frame": 0, "points": [[1, 2, 3]]}],
        }
        show = DroneShow.from_dict(data)
        # Y-up (x,y,z)=(1,2,3) -> Z-up (x,-z,y)=(1,-3,2)
        p = show.frames[0].points[0]
        self.assertEqual(p.as_tuple(), (1.0, -3.0, 2.0))

    def test_scale_and_offset(self):
        data = {"fps": 1, "frame_start": 0,
                "frames": [{"frame": 0, "points": [[1, 1, 1]]}]}
        show = DroneShow.from_dict(data, scale=2.0, offset=Vec3(10, 0, 0))
        self.assertEqual(show.frames[0].points[0].as_tuple(), (12.0, 2.0, 2.0))


class TestSampling(unittest.TestCase):
    def setUp(self):
        self.show = DroneShow.from_dict({
            "fps": 1, "frame_start": 0,
            "frames": [
                {"frame": 0, "points": [[0, 0, 0]]},
                {"frame": 2, "points": [[0, 0, 10]]},
            ],
        })

    def test_interpolation_midpoint(self):
        p = self.show.points_at(1.0)[0]
        self.assertAlmostEqual(p.z, 5.0)

    def test_clamp_before_and_after(self):
        self.assertAlmostEqual(self.show.points_at(-5)[0].z, 0.0)
        self.assertAlmostEqual(self.show.points_at(99)[0].z, 10.0)

    def test_centroid_and_bounds(self):
        show = generate_sample_show(duration_s=2.0)
        c = show.centroid_at(1.0)
        self.assertIsInstance(c, Vec3)
        mn, mx = show.bounds()
        self.assertLess(mn.z, mx.z)
        self.assertGreater(show.max_radius(), 0.0)

    def test_bounding_sphere_contains_points(self):
        show = generate_sample_show(duration_s=2.0)
        c, r = show.bounding_sphere_at(0.5)
        for p in show.points_at(0.5):
            self.assertLessEqual((p - c).length(), r + 1e-6)


if __name__ == "__main__":
    unittest.main()
