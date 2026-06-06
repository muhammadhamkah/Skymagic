import math
import unittest

from dronecam.geometry import (
    GeoOrigin,
    Vec3,
    angle_diff,
    camera_basis,
    look_at_angles,
    lerp_angle,
)


class TestVec3(unittest.TestCase):
    def test_arithmetic(self):
        a = Vec3(1, 2, 3)
        b = Vec3(4, 5, 6)
        self.assertEqual((a + b).as_tuple(), (5, 7, 9))
        self.assertEqual((b - a).as_tuple(), (3, 3, 3))
        self.assertEqual((a * 2).as_tuple(), (2, 4, 6))
        self.assertEqual((2 * a).as_tuple(), (2, 4, 6))

    def test_dot_cross_length(self):
        x = Vec3(1, 0, 0)
        y = Vec3(0, 1, 0)
        self.assertAlmostEqual(x.dot(y), 0.0)
        self.assertEqual(x.cross(y).as_tuple(), (0, 0, 1))
        self.assertAlmostEqual(Vec3(3, 4, 0).length(), 5.0)
        self.assertAlmostEqual(Vec3(0, 0, 5).normalized().z, 1.0)


class TestAngles(unittest.TestCase):
    def test_angle_diff_wraps(self):
        self.assertAlmostEqual(angle_diff(math.radians(10), math.radians(350)),
                               math.radians(20), places=6)

    def test_lerp_angle_shortest(self):
        a = math.radians(350)
        b = math.radians(10)
        mid = lerp_angle(a, b, 0.5)
        # midpoint of the short arc from 350deg to 10deg is 0deg (360deg)
        self.assertAlmostEqual(math.cos(mid), 1.0, places=6)
        self.assertAlmostEqual(math.sin(mid), 0.0, places=6)


class TestCameraBasis(unittest.TestCase):
    def test_forward_east_level(self):
        f, r, u = camera_basis(yaw=0.0, pitch=0.0)
        self.assertAlmostEqual(f.x, 1.0, places=6)
        self.assertAlmostEqual(f.z, 0.0, places=6)
        # with world up=+z and forward=+x, right=+y (north) and up=+z
        self.assertAlmostEqual(r.y, 1.0, places=6)
        self.assertAlmostEqual(u.z, 1.0, places=6)
        # basis is orthonormal
        self.assertAlmostEqual(f.dot(r), 0.0, places=6)
        self.assertAlmostEqual(f.dot(u), 0.0, places=6)
        self.assertAlmostEqual(r.dot(u), 0.0, places=6)

    def test_straight_down_not_degenerate(self):
        f, r, u = camera_basis(yaw=0.5, pitch=-math.pi / 2)
        self.assertAlmostEqual(f.z, -1.0, places=6)
        self.assertAlmostEqual(r.length(), 1.0, places=6)
        self.assertAlmostEqual(u.length(), 1.0, places=6)

    def test_look_at(self):
        yaw, pitch = look_at_angles(Vec3(0, 0, 0), Vec3(0, 0, 10))
        self.assertAlmostEqual(pitch, math.pi / 2, places=6)
        yaw, pitch = look_at_angles(Vec3(0, 0, 0), Vec3(10, 0, 0))
        self.assertAlmostEqual(yaw, 0.0, places=6)
        self.assertAlmostEqual(pitch, 0.0, places=6)


class TestGeoOrigin(unittest.TestCase):
    def test_origin_maps_to_itself(self):
        o = GeoOrigin(1.2897, 103.8501, 0.0)
        lat, lon, alt = o.to_gps(Vec3(0, 0, 0))
        self.assertAlmostEqual(lat, 1.2897, places=9)
        self.assertAlmostEqual(lon, 103.8501, places=9)
        self.assertAlmostEqual(alt, 0.0, places=9)

    def test_north_increases_latitude(self):
        o = GeoOrigin(0.0, 0.0, 0.0)
        lat, lon, alt = o.to_gps(Vec3(0, 111.0, 5.0))  # ~111 m north
        self.assertGreater(lat, 0.0)
        self.assertAlmostEqual(lon, 0.0, places=6)
        self.assertAlmostEqual(alt, 5.0, places=6)

    def test_east_increases_longitude(self):
        o = GeoOrigin(0.0, 0.0, 0.0)
        lat, lon, alt = o.to_gps(Vec3(111.0, 0.0, 0.0))
        self.assertGreater(lon, 0.0)
        self.assertAlmostEqual(lat, 0.0, places=6)

    def test_heading_rotates_frame(self):
        # heading 90: local +y points East -> moving +y should change longitude
        o = GeoOrigin(0.0, 0.0, 0.0, heading_deg=90.0)
        lat, lon, _ = o.to_gps(Vec3(0, 100.0, 0.0))
        self.assertGreater(lon, 0.0)
        self.assertAlmostEqual(lat, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
