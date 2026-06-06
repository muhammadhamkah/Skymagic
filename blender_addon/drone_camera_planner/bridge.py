"""Glue between the Blender scene and the ``dronecam`` planning engine.

Responsibilities:

* read the drone objects from the scene and sample them into a
  :class:`dronecam.show.DroneShow`,
* run the planner to get a :class:`dronecam.planner.CameraPath`,
* materialise that path as a real **animated Blender camera** so the user can
  scrub and look through it, and
* export the mission files.

``bpy`` / ``mathutils`` are imported lazily inside the functions so this module
can be imported (and partially unit-tested) outside Blender. Everything in
``dronecam`` is pure standard library and imports fine anywhere.
"""

from dronecam.camera import CameraConfig, get_preset
from dronecam.export import export_mission
from dronecam.geometry import GeoOrigin, Vec3, forward_vector
from dronecam.planner import AutoPlanOptions, plan_auto_path
from dronecam.segments import SegmentPlanOptions, ShotSegment, plan_segments
from dronecam.show import DroneShow, ShowFrame
from dronecam.simulation import simulate

CAMERA_OBJECT_NAME = "DroneCam"
PATH_OBJECT_NAME = "DroneCamPath"

# Hard cap on how many frames we sample from the timeline. Each sample triggers
# a full scene evaluation, so this bounds the (UI-blocking) planning cost
# regardless of show length; ~150 samples is plenty for a smooth camera path.
MAX_SHOW_SAMPLES = 150


class PlannerError(Exception):
    """Raised for user-facing problems (no drones, no path, etc.)."""


# --------------------------------------------------------------------------
# Reading the show out of the scene
# --------------------------------------------------------------------------
def get_drone_objects(context):
    import bpy

    p = context.scene.dcp
    if p.drone_source == "COLLECTION":
        coll = bpy.data.collections.get(p.drone_collection)
        if coll is None:
            raise PlannerError(f"Collection '{p.drone_collection}' not found.")
        objs = list(coll.all_objects)
    elif p.drone_source == "SELECTED":
        objs = list(context.selected_objects)
    else:  # PREFIX
        prefix = p.drone_prefix
        objs = [o for o in context.scene.objects if o.name.startswith(prefix)]

    if not objs:
        raise PlannerError(
            "No drone objects found. Check the prefix / collection / selection."
        )
    # Stable ordering keeps drone indices consistent across frames.
    return sorted(objs, key=lambda o: o.name)


def _frame_range(context):
    p = context.scene.dcp
    if p.use_scene_range:
        return context.scene.frame_start, context.scene.frame_end
    return p.frame_start, p.frame_end


def _fps(context):
    r = context.scene.render
    return r.fps / r.fps_base


def sample_show(context):
    """Sample drone world positions across the frame range into a DroneShow."""
    import bpy  # noqa: F401  (ensure we're in Blender)

    p = context.scene.dcp
    objs = get_drone_objects(context)
    f_start, f_end = _frame_range(context)
    if f_end <= f_start:
        raise PlannerError("Frame end must be greater than frame start.")
    fps = _fps(context)
    scene = context.scene

    # Each sampled frame forces a full scene evaluation (heavy with hundreds of
    # drones), so cap the total number of samples. Planning a smooth path only
    # needs a coarse time resolution; raise the step if the user's step would
    # produce more than MAX_SHOW_SAMPLES frames.
    span = f_end - f_start
    step = max(p.frame_step, 1, -(-span // MAX_SHOW_SAMPLES))  # ceil div
    sample_frames = list(range(f_start, f_end + 1, step))

    frames = []
    original = scene.frame_current
    wm = context.window_manager
    wm.progress_begin(0, len(sample_frames))
    try:
        for i, f in enumerate(sample_frames):
            scene.frame_set(f)
            deps = context.evaluated_depsgraph_get()
            points = []
            for obj in objs:
                loc = obj.evaluated_get(deps).matrix_world.translation
                points.append(Vec3(loc.x, loc.y, loc.z))
            frames.append(ShowFrame(t=(f - f_start) / fps, points=points))
            wm.progress_update(i)
            if i % 20 == 0:
                print(f"[DroneCam] sampling show {i + 1}/{len(sample_frames)}")
    finally:
        wm.progress_end()
        scene.frame_set(original)

    print(f"[DroneCam] sampled {len(frames)} frames x {len(objs)} drones "
          f"(step {step})")
    name = bpy.path.display_name_from_filepath(bpy.data.filepath) or "Blender Show"
    return DroneShow(name=name, fps=fps, frames=frames,
                     drone_ids=[o.name for o in objs])


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
def make_camera_config(context) -> CameraConfig:
    return get_preset(context.scene.dcp.preset)


def make_options(context) -> AutoPlanOptions:
    p = context.scene.dcp
    return AutoPlanOptions(
        mode=p.plan_mode,
        keyframe_count=p.keyframes,
        elevation_deg=p.elevation,
        start_azimuth_deg=p.azimuth,
        orbit_degrees=p.orbit_degrees,
        fill=p.fill,
        min_altitude_m=p.min_altitude,
    )


def _waypoint_positions(coll):
    """World positions of the Empties in a waypoint collection, in order.

    Ordered by the integer ``dcp_order`` custom property (set when added), then
    by name as a fallback.
    """
    if coll is None:
        return []
    objs = sorted(coll.objects, key=lambda o: (o.get("dcp_order", 0), o.name))
    return [Vec3(*o.matrix_world.translation) for o in objs]


def _curve_positions(obj):
    """World-space control points of a (drawn) curve, in stroke order."""
    if obj is None or obj.type != "CURVE":
        return []
    mw = obj.matrix_world
    pts = []
    for sp in obj.data.splines:
        if sp.type == "BEZIER":
            for bp in sp.bezier_points:
                w = mw @ bp.co
                pts.append(Vec3(w.x, w.y, w.z))
        else:
            for p in sp.points:
                w = mw @ p.co.to_3d()
                pts.append(Vec3(w.x, w.y, w.z))
    return pts


def _segment_positions(seg):
    """A scene's camera positions: the drawn curve if set, else its Empties."""
    if seg.path_curve is not None:
        pts = _curve_positions(seg.path_curve)
        if pts:
            return pts
    return _waypoint_positions(seg.waypoints)


def read_segments(context):
    """Build :class:`ShotSegment` list from the scene's segment properties.

    Scene frames are converted to show-seconds using the same origin as the
    sampled show, so segment times line up with the drone cloud.
    """
    p = context.scene.dcp
    f_start, _ = _frame_range(context)
    fps = _fps(context)
    segs = []
    for s in p.segments:
        positions = _segment_positions(s)
        if not positions:
            continue
        segs.append(ShotSegment(
            name=s.name,
            t_start=(s.start_frame - f_start) / fps,
            t_end=(s.end_frame - f_start) / fps,
            waypoints=positions,
            recording=s.recording,
        ))
    return segs


def build_path(context, show, camera):
    """Plan a camera path: scene-by-scene if segments exist, else auto-orbit."""
    p = context.scene.dcp
    segs = read_segments(context)
    if segs:
        opt = SegmentPlanOptions(
            bake_hz=p.bake_hz,
            fill=p.fill,
            record_transitions=p.record_transitions,
            standby_s=p.standby_s,
        )
        return plan_segments(show, camera, segs, opt)
    return plan_auto_path(show, camera, make_options(context))


def plan_and_build(context):
    """Sample, plan, build the animated camera, and run a coverage simulation.

    Returns ``(show, path, camera, sim_result)``.
    """
    show = sample_show(context)
    camera = make_camera_config(context)
    path = build_path(context, show, camera)
    if not path.keyframes:
        raise PlannerError(
            "Empty path. Add scenes with waypoint Empties, or check the show."
        )
    build_camera_object(context, show, path, camera)
    build_path_visual(context, show, camera, path)
    result = simulate(show, camera, path,
                      safety_radius_m=context.scene.dcp.safety_radius)
    return show, path, camera, result


# --------------------------------------------------------------------------
# Building the animated Blender camera
# --------------------------------------------------------------------------
def _get_or_create_camera(context):
    import bpy

    obj = context.scene.objects.get(CAMERA_OBJECT_NAME)
    if obj is not None and obj.type == "CAMERA":
        # clear any previous animation so re-planning is clean
        obj.animation_data_clear()
        obj.data.animation_data_clear()
        return obj

    cam_data = bpy.data.cameras.new(CAMERA_OBJECT_NAME)
    obj = bpy.data.objects.new(CAMERA_OBJECT_NAME, cam_data)
    context.scene.collection.objects.link(obj)
    return obj


def _set_linear(obj):
    """Force LINEAR interpolation so the preview matches the planner/sim."""
    ad = obj.animation_data
    if ad and ad.action:
        for fc in ad.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"


def build_camera_object(context, show, path, camera: CameraConfig):
    """Create/replace the ``DroneCam`` object and key it along the path.

    Blender cameras look down local ``-Z`` with ``+Y`` up; we orient each
    keyframe with ``to_track_quat('-Z', 'Y')`` from the pose's forward vector.
    Sensor and (animated) focal length are set to match the camera config so the
    viewport framing matches dronecam's framing engine.
    """
    from mathutils import Vector

    obj = _get_or_create_camera(context)
    cam_data = obj.data
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = camera.sensor_width_mm

    f_start, _ = _frame_range(context)
    fps = _fps(context)

    for kf in path.keyframes:
        pose = path.pose_at(kf.t, camera, show)
        # kf.t is seconds from the sampled show's origin (f_start), so each
        # keyframe lands on its real scene frame.
        frame = f_start + round(kf.t * fps)

        obj.location = (pose.position.x, pose.position.y, pose.position.z)
        fwd = forward_vector(pose.yaw, pose.pitch)
        quat = Vector((fwd.x, fwd.y, fwd.z)).to_track_quat("-Z", "Y")
        obj.rotation_euler = quat.to_euler()
        cam_data.lens = pose.focal_mm

        obj.keyframe_insert("location", frame=frame)
        obj.keyframe_insert("rotation_euler", frame=frame)
        cam_data.keyframe_insert("lens", frame=frame)

    _set_linear(obj)
    _set_linear(cam_data)

    # Make it the active scene camera so Numpad 0 previews the shot.
    context.scene.camera = obj
    return obj


def build_path_visual(context, show, camera: CameraConfig, path):
    """Draw the whole camera trajectory as a visible tube (start -> end).

    A persistent ``DroneCamPath`` curve so the operator can verify, at a glance,
    that the camera never flies into the show. Rebuilt on every plan.
    """
    import bpy

    old = bpy.data.objects.get(PATH_OBJECT_NAME)
    if old is not None:
        data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if isinstance(data, bpy.types.Curve) and data.users == 0:
            bpy.data.curves.remove(data)

    pts = [path.pose_at(kf.t, camera, show).position for kf in path.keyframes]
    if len(pts) < 2:
        return None

    curve = bpy.data.curves.new(PATH_OBJECT_NAME, "CURVE")
    curve.dimensions = "3D"
    sp = curve.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        sp.points[i].co = (p.x, p.y, p.z, 1.0)
    curve.bevel_depth = 1.0  # ~1 m tube so it reads at venue scale

    mat = bpy.data.materials.get("DroneCamPathMat")
    if mat is None:
        mat = bpy.data.materials.new("DroneCamPathMat")
        mat.diffuse_color = (1.0, 0.35, 0.0, 1.0)  # orange
    curve.materials.append(mat)

    obj = bpy.data.objects.new(PATH_OBJECT_NAME, curve)
    obj.show_in_front = True
    context.scene.collection.objects.link(obj)
    return obj


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def export(context):
    import bpy

    p = context.scene.dcp
    show = sample_show(context)
    camera = make_camera_config(context)
    path = build_path(context, show, camera)
    if not path.keyframes:
        raise PlannerError("Nothing to export — the planned path is empty.")

    origin = GeoOrigin(
        latitude=p.origin_lat,
        longitude=p.origin_lon,
        altitude=p.origin_alt,
        heading_deg=p.origin_heading,
    )
    out_dir = bpy.path.abspath(p.export_dir)
    mission = export_mission(path, show, origin, camera, out_dir=out_dir)
    return list(mission["files"].values())
