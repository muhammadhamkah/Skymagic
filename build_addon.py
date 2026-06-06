#!/usr/bin/env python3
"""Bundle the Blender add-on into an installable .zip.

The add-on glue lives in ``blender_addon/drone_camera_planner/``. This script
copies it into ``dist/`` together with a vendored copy of the dependency-free
``dronecam`` package, then zips it so it can be installed via Blender's
*Edit > Preferences > Add-ons > Install...*.

Usage::

    python build_addon.py

Produces ``dist/drone_camera_planner.zip`` (and an unzipped ``dist/drone_camera_planner/``
for inspection). The vendored ``dronecam`` is a copy — the source of truth is the
top-level ``dronecam/`` package.
"""

from __future__ import annotations

import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON_SRC = os.path.join(ROOT, "blender_addon", "drone_camera_planner")
DRONECAM_SRC = os.path.join(ROOT, "dronecam")
DIST = os.path.join(ROOT, "dist")
ADDON_OUT = os.path.join(DIST, "drone_camera_planner")
ZIP_OUT = os.path.join(DIST, "drone_camera_planner.zip")


def _ignore(_dir, names):
    return [n for n in names if n in {"__pycache__"} or n.endswith(".pyc")]


def main() -> None:
    if os.path.exists(ADDON_OUT):
        shutil.rmtree(ADDON_OUT)
    os.makedirs(ADDON_OUT, exist_ok=True)

    # 1. add-on glue
    shutil.copytree(ADDON_SRC, ADDON_OUT, dirs_exist_ok=True, ignore=_ignore)
    # 2. vendored dronecam package
    vendor = os.path.join(ADDON_OUT, "vendor", "dronecam")
    shutil.copytree(DRONECAM_SRC, vendor, ignore=_ignore)

    # 3. zip it (Blender expects the add-on package dir at the zip root)
    if os.path.exists(ZIP_OUT):
        os.remove(ZIP_OUT)
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirs, files in os.walk(ADDON_OUT):
            for fn in files:
                full = os.path.join(dirpath, fn)
                arc = os.path.relpath(full, DIST)
                zf.write(full, arc)

    print(f"Built add-on: {ZIP_OUT}")
    print("Install it in Blender via Edit > Preferences > Add-ons > Install...")


if __name__ == "__main__":
    main()
