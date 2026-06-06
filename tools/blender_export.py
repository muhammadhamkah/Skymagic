"""Export a drone-show animation from Blender to the dronecam JSON format.

Run this *inside Blender* (it depends on the ``bpy`` module which only exists in
Blender's Python). Two ways to invoke it:

1. From Blender's Scripting workspace: open this file and press *Run Script*.
2. Headless from a shell::

       blender show.blend --background --python tools/blender_export.py -- \
           --collection "Drones" --output show.json

How drones are identified
--------------------------
By default every mesh/empty object in the chosen collection (default:
``Drones``) is treated as one drone and its world-space origin is sampled each
frame. Point the ``--collection`` at the collection that holds your show
objects. If you instead drive drones with a particle system or geometry nodes,
adapt ``collect_drone_objects`` to yield one matrix/location per drone.

The output uses Blender's native Z-up frame, which is also dronecam's world
frame, so no axis remap is needed (``up_axis`` is written as ``Z``).
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import bpy  # type: ignore
except ImportError:  # pragma: no cover - only meaningful inside Blender
    print("This script must be run inside Blender (no 'bpy' module found).")
    sys.exit(1)


def parse_args(argv):
    # Blender passes script args after a literal "--".
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser(description="Export drone show to dronecam JSON")
    p.add_argument("--collection", default="Drones",
                   help="name of the collection holding the drone objects")
    p.add_argument("--output", default="show.json")
    p.add_argument("--scale", type=float, default=1.0,
                   help="multiply all coordinates (e.g. if scene is not in metres)")
    p.add_argument("--name", default=None, help="show name (defaults to .blend name)")
    return p.parse_args(argv)


def collect_drone_objects(collection_name):
    coll = bpy.data.collections.get(collection_name)
    if coll is None:
        raise SystemExit(f"Collection '{collection_name}' not found in scene.")
    # Stable ordering so drone indices are consistent across frames.
    return sorted(coll.all_objects, key=lambda o: o.name)


def main():
    args = parse_args(sys.argv)
    scene = bpy.context.scene
    objects = collect_drone_objects(args.collection)
    if not objects:
        raise SystemExit(f"No objects in collection '{args.collection}'.")

    fps = scene.render.fps / scene.render.fps_base
    f_start = scene.frame_start
    f_end = scene.frame_end

    frames = []
    for f in range(f_start, f_end + 1):
        scene.frame_set(f)
        points = []
        for obj in objects:
            loc = obj.matrix_world.translation
            points.append([loc.x * args.scale, loc.y * args.scale, loc.z * args.scale])
        frames.append({"frame": f, "points": points})

    data = {
        "name": args.name or bpy.path.display_name_from_filepath(bpy.data.filepath) or "Blender Show",
        "fps": fps,
        "frame_start": f_start,
        "frame_end": f_end,
        "up_axis": "Z",
        "units": "meters",
        "drone_ids": [o.name for o in objects],
        "frames": frames,
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"Exported {len(objects)} drones x {len(frames)} frames -> {args.output}")


if __name__ == "__main__":
    main()
