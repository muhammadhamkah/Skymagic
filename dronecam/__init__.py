"""Autonomous Drone Show Capture Planner.

``dronecam`` turns a pre-planned drone show (exported from Blender) into an
autonomous camera-drone filming mission. The pipeline is:

    show import  ->  camera config  ->  path planning  ->  framing/simulation
                 ->  mission export

Everything in this package is implemented with the Python standard library so
it runs anywhere without native dependencies.
"""

from .geometry import Vec3, GeoOrigin
from .show import DroneShow, ShowFrame
from .camera import CameraConfig, CameraPose
from .planner import CameraPath, PathKeyframe, plan_auto_path
from .segments import ShotSegment, SegmentPlanOptions, plan_segments
from .framing import FramingEngine, FrameMetrics
from .simulation import simulate, SimulationResult
from .export import export_mission

__all__ = [
    "Vec3",
    "GeoOrigin",
    "DroneShow",
    "ShowFrame",
    "CameraConfig",
    "CameraPose",
    "CameraPath",
    "PathKeyframe",
    "plan_auto_path",
    "ShotSegment",
    "SegmentPlanOptions",
    "plan_segments",
    "FramingEngine",
    "FrameMetrics",
    "simulate",
    "SimulationResult",
    "export_mission",
]

__version__ = "0.1.0"
