"""Drone Camera Planner — Blender add-on.

Plan an autonomous camera-drone flight for a drone show, right inside Blender:

* read the show's drones straight from the scene (by name prefix, collection or
  selection),
* generate an orbit / static / flyby camera path that keeps the show framed,
* build a real **animated camera** you can scrub and look through (Numpad 0) to
  preview the footage, and
* export a real-world waypoint mission (GPS + gimbal + zoom + recording).

The heavy lifting lives in the dependency-free ``dronecam`` package, vendored
next to this add-on (see ``build_addon.py``). The glue that talks to Blender is
in ``bridge.py``.
"""

import os
import sys

bl_info = {
    "name": "Drone Camera Planner",
    "author": "Skymagic",
    "version": (0, 1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Drone Cam",
    "description": "Plan an autonomous camera-drone flight for a drone show and export a mission",
    "category": "Object",
}


def _ensure_dronecam():
    """Make the ``dronecam`` package importable from inside Blender.

    Tries, in order: an already-importable ``dronecam``; a vendored copy shipped
    beside this add-on (``vendor/``, populated by ``build_addon.py``); and, for
    in-repo development, the repository root two levels up.
    """
    try:
        import dronecam  # noqa: F401
        return
    except ImportError:
        pass

    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, "vendor"),
        os.path.abspath(os.path.join(here, "..", "..")),  # repo root in dev
    ]
    for path in candidates:
        if os.path.isdir(os.path.join(path, "dronecam")) and path not in sys.path:
            sys.path.insert(0, path)
    import dronecam  # noqa: F401  (raises if still not found)


_ensure_dronecam()

import bpy  # noqa: E402
from bpy.props import (  # noqa: E402
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
    PointerProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList  # noqa: E402

from . import bridge  # noqa: E402


# --------------------------------------------------------------------------
# A single scene / shot
# --------------------------------------------------------------------------
class DCPSegment(PropertyGroup):
    name: StringProperty(name="Name", default="Scene")
    start_frame: IntProperty(name="Start", default=1)
    end_frame: IntProperty(name="End", default=100)
    waypoints: PointerProperty(
        name="Waypoints",
        type=bpy.types.Collection,
        description="Collection of waypoint Empties for this scene's camera move",
    )
    recording: BoolProperty(name="Recording", default=True)


# --------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------
class DCPProperties(PropertyGroup):
    # --- which objects are the drones ---
    drone_source: EnumProperty(
        name="Drones",
        items=[
            ("PREFIX", "Name prefix", "Objects whose name starts with the prefix"),
            ("COLLECTION", "Collection", "Objects in a named collection"),
            ("SELECTED", "Selection", "The currently selected objects"),
        ],
        default="PREFIX",
    )
    drone_prefix: StringProperty(name="Prefix", default="UAV_")
    drone_collection: StringProperty(name="Collection", default="")

    # --- frame range / sampling ---
    use_scene_range: BoolProperty(name="Use scene frame range", default=True)
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=250)
    frame_step: IntProperty(
        name="Sample step", default=25, min=1,
        description="Sample every Nth frame (raise for very long shows)",
    )

    # --- camera ---
    preset: EnumProperty(
        name="Camera",
        items=[
            ("generic", "Generic Cinewhoop", ""),
            ("cine-mini", "Cinema Mini 4K", ""),
            ("long-lens", "Heavy Lift + Zoom", ""),
        ],
        default="generic",
    )

    # --- planning ---
    plan_mode: EnumProperty(
        name="Mode",
        items=[
            ("orbit", "Orbit", "Sweep an arc around the formation"),
            ("static", "Static", "Hold a vantage; gimbal/zoom track the show"),
            ("flyby", "Flyby", "Fly a straight line past the show"),
        ],
        default="orbit",
    )
    keyframes: IntProperty(name="Keyframes", default=24, min=2, max=400)
    elevation: FloatProperty(name="Elevation (deg)", default=18.0)
    azimuth: FloatProperty(name="Azimuth (deg)", default=200.0)
    orbit_degrees: FloatProperty(name="Orbit sweep (deg)", default=90.0)
    fill: FloatProperty(name="Frame fill", default=0.72, min=0.1, max=1.0)
    min_altitude: FloatProperty(name="Min altitude (m)", default=3.0, min=0.0)

    # --- venue origin (for GPS export) ---
    origin_lat: FloatProperty(name="Latitude", default=0.0, precision=7)
    origin_lon: FloatProperty(name="Longitude", default=0.0, precision=7)
    origin_alt: FloatProperty(name="Altitude (m)", default=0.0)
    origin_heading: FloatProperty(
        name="Heading (deg)", default=0.0,
        description="Compass bearing of the scene's +Y axis",
    )
    export_dir: StringProperty(name="Export to", subtype="DIR_PATH", default="//mission")

    # --- scenes / shots ---
    segments: CollectionProperty(type=DCPSegment)
    active_segment: IntProperty(default=0)
    bake_hz: FloatProperty(
        name="Smoothness (kf/s)", default=2.0, min=0.5, max=10.0,
        description="Keyframes baked per second along the smoothed path",
    )
    record_transitions: BoolProperty(
        name="Record during transitions", default=True,
        description="Keep recording while repositioning between scenes",
    )

    # --- last report (read-only display) ---
    last_report: StringProperty(name="Report", default="")


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------
class DCP_OT_sync_range(Operator):
    bl_idname = "dcp.sync_range"
    bl_label = "Use scene range"
    bl_description = "Copy the scene's frame range into the planner"

    def execute(self, context):
        p = context.scene.dcp
        p.frame_start = context.scene.frame_start
        p.frame_end = context.scene.frame_end
        return {"FINISHED"}


class DCP_UL_segments(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="SEQUENCE")
        n = len(item.waypoints.objects) if item.waypoints else 0
        row.label(text=f"{item.start_frame}-{item.end_frame}  ({n} wp)")


def _active_segment(context):
    p = context.scene.dcp
    if 0 <= p.active_segment < len(p.segments):
        return p.segments[p.active_segment]
    return None


class DCP_OT_segment_add(Operator):
    bl_idname = "dcp.segment_add"
    bl_label = "Add Scene"
    bl_description = "Add a scene/shot and a fresh collection to hold its waypoints"

    def execute(self, context):
        import bpy

        p = context.scene.dcp
        seg = p.segments.add()
        idx = len(p.segments)
        seg.name = f"Scene {idx}"
        seg.start_frame = context.scene.frame_current
        seg.end_frame = min(context.scene.frame_end, context.scene.frame_current + 100)
        coll = bpy.data.collections.new(f"DroneCam WP {idx}")
        context.scene.collection.children.link(coll)
        seg.waypoints = coll
        p.active_segment = idx - 1
        return {"FINISHED"}


class DCP_OT_segment_remove(Operator):
    bl_idname = "dcp.segment_remove"
    bl_label = "Remove Scene"
    bl_description = "Remove the selected scene (its waypoint collection is left in place)"

    def execute(self, context):
        p = context.scene.dcp
        if 0 <= p.active_segment < len(p.segments):
            p.segments.remove(p.active_segment)
            p.active_segment = max(0, p.active_segment - 1)
        return {"FINISHED"}


class DCP_OT_segment_grab_start(Operator):
    bl_idname = "dcp.segment_grab_start"
    bl_label = "Set start = playhead"

    def execute(self, context):
        seg = _active_segment(context)
        if seg:
            seg.start_frame = context.scene.frame_current
        return {"FINISHED"}


class DCP_OT_segment_grab_end(Operator):
    bl_idname = "dcp.segment_grab_end"
    bl_label = "Set end = playhead"

    def execute(self, context):
        seg = _active_segment(context)
        if seg:
            seg.end_frame = context.scene.frame_current
        return {"FINISHED"}


class DCP_OT_add_waypoint(Operator):
    bl_idname = "dcp.add_waypoint"
    bl_label = "Add Waypoint at Cursor"
    bl_description = ("Drop a camera waypoint Empty at the 3D cursor for the "
                      "selected scene (Shift+Right-click places the cursor)")

    def execute(self, context):
        import bpy

        seg = _active_segment(context)
        if seg is None:
            self.report({"ERROR"}, "Add a scene first.")
            return {"CANCELLED"}
        coll = seg.waypoints
        if coll is None:
            coll = bpy.data.collections.new(f"DroneCam WP {context.scene.dcp.active_segment + 1}")
            context.scene.collection.children.link(coll)
            seg.waypoints = coll

        order = len(coll.objects)
        empty = bpy.data.objects.new(f"WP_{seg.name}_{order}", None)
        empty.empty_display_type = "SPHERE"
        empty.empty_display_size = 2.0
        empty.location = context.scene.cursor.location
        empty["dcp_order"] = order
        coll.objects.link(empty)
        self.report({"INFO"}, f"Added waypoint {order + 1} to '{seg.name}'")
        return {"FINISHED"}


class DCP_OT_generate(Operator):
    bl_idname = "dcp.generate"
    bl_label = "Generate Camera Path"
    bl_description = "Sample the show, plan the camera path, and build an animated camera to preview"

    def execute(self, context):
        try:
            show, path, camera, result = bridge.plan_and_build(context)
        except bridge.PlannerError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        context.scene.dcp.last_report = result.report()
        self.report(
            {"INFO"},
            f"Coverage {result.coverage_score:.0%}, "
            f"max speed {result.max_speed_mps:.1f} m/s, "
            f"{result.violation_count} warnings",
        )
        return {"FINISHED"}


class DCP_OT_export(Operator):
    bl_idname = "dcp.export"
    bl_label = "Export Mission"
    bl_description = "Export waypoint mission files (JSON / Litchi CSV / KML / timeline)"

    def execute(self, context):
        try:
            files = bridge.export(context)
        except bridge.PlannerError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(files)} files to {os.path.dirname(files[0])}")
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Panel
# --------------------------------------------------------------------------
class DCP_PT_panel(Panel):
    bl_label = "Drone Camera Planner"
    bl_idname = "DCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Drone Cam"

    def draw(self, context):
        layout = self.layout
        p = context.scene.dcp

        box = layout.box()
        box.label(text="Show drones", icon="OUTLINER_OB_POINTCLOUD")
        box.prop(p, "drone_source", text="")
        if p.drone_source == "PREFIX":
            box.prop(p, "drone_prefix")
        elif p.drone_source == "COLLECTION":
            box.prop_search(p, "drone_collection", bpy.data, "collections", text="")

        box = layout.box()
        box.label(text="Frame range", icon="TIME")
        box.prop(p, "use_scene_range")
        if not p.use_scene_range:
            row = box.row(align=True)
            row.prop(p, "frame_start")
            row.prop(p, "frame_end")
        box.operator("dcp.sync_range", icon="PREVIEW_RANGE")
        box.prop(p, "frame_step")

        box = layout.box()
        box.label(text="Camera", icon="CAMERA_DATA")
        box.prop(p, "preset")
        box.prop(p, "fill")

        box = layout.box()
        box.label(text="Scenes / shots", icon="SEQUENCE")
        row = box.row()
        row.template_list("DCP_UL_segments", "", p, "segments",
                          p, "active_segment", rows=3)
        col = row.column(align=True)
        col.operator("dcp.segment_add", text="", icon="ADD")
        col.operator("dcp.segment_remove", text="", icon="REMOVE")

        seg = _active_segment(context)
        if seg is not None:
            sub = box.column(align=True)
            sub.prop(seg, "name")
            r = sub.row(align=True)
            r.prop(seg, "start_frame")
            r.operator("dcp.segment_grab_start", text="", icon="TRIA_DOWN_BAR")
            r = sub.row(align=True)
            r.prop(seg, "end_frame")
            r.operator("dcp.segment_grab_end", text="", icon="TRIA_UP_BAR")
            sub.prop_search(seg, "waypoints", bpy.data, "collections", text="Waypoints")
            sub.prop(seg, "recording")
            sub.operator("dcp.add_waypoint", icon="EMPTY_AXIS")
        else:
            box.label(text="Add a scene, then drop waypoint Empties.", icon="INFO")
        box.prop(p, "bake_hz")
        box.prop(p, "record_transitions")

        if len(p.segments) == 0:
            box = layout.box()
            box.label(text="Auto shot (used when no scenes)", icon="CON_FOLLOWPATH")
            box.prop(p, "plan_mode")
            box.prop(p, "keyframes")
            box.prop(p, "elevation")
            box.prop(p, "azimuth")
            if p.plan_mode == "orbit":
                box.prop(p, "orbit_degrees")
            box.prop(p, "min_altitude")

        layout.operator("dcp.generate", icon="OUTLINER_OB_CAMERA")

        if p.last_report:
            box = layout.box()
            box.label(text="Coverage report", icon="INFO")
            for line in p.last_report.splitlines():
                box.label(text=line)

        box = layout.box()
        box.label(text="Venue origin (GPS export)", icon="WORLD")
        box.prop(p, "origin_lat")
        box.prop(p, "origin_lon")
        box.prop(p, "origin_alt")
        box.prop(p, "origin_heading")
        box.prop(p, "export_dir")
        layout.operator("dcp.export", icon="EXPORT")


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
_classes = (
    DCPSegment,        # must register before DCPProperties references it
    DCPProperties,
    DCP_UL_segments,
    DCP_OT_sync_range,
    DCP_OT_segment_add,
    DCP_OT_segment_remove,
    DCP_OT_segment_grab_start,
    DCP_OT_segment_grab_end,
    DCP_OT_add_waypoint,
    DCP_OT_generate,
    DCP_OT_export,
    DCP_PT_panel,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.dcp = PointerProperty(type=DCPProperties)


def unregister():
    del bpy.types.Scene.dcp
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
