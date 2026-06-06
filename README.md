# Autonomous Drone Show Capture Planner

Turn a pre-planned drone show into an **autonomous filming mission** for a
camera drone. Drone shows are already designed as precise 3D simulations before
they ever fly — so the camera that films them can be planned in advance too,
instead of being flown live by a pilot reacting to formations.

`dronecam` imports a drone show animation (e.g. exported from Blender), lets you
design or auto-generate a virtual camera path, **simulates** the resulting
footage to check the show stays framed, and **exports** a real-world waypoint
mission (GPS + gimbal + zoom + recording triggers) that captures the show
automatically on site.

> MVP scope (v1): import a Blender animation → plan a camera path → simulate the
> capture → export a mission file, for a single camera drone. The whole pipeline
> runs on the Python standard library — no native dependencies.

---

## Pipeline

```
 Blender show        camera config       path planning        simulation            mission export
 ───────────    →    ─────────────   →   ─────────────    →   ──────────────    →   ───────────────
 .blend/.json        optics/gimbal/      manual / assisted    framing + coverage     mission.json
 (drone tracks)      flight envelope     / automatic          + limit checks         Litchi CSV / KML
```

| Stage | Module | What it does |
|-------|--------|--------------|
| Import | `dronecam.show` | Read a show (JSON/CSV), map it into a metric world frame (x=East, y=North, z=Up). |
| Camera | `dronecam.camera` | Sensor/lens/FOV, zoom range, gimbal limits, flight envelope, presets. |
| Planning | `dronecam.planner` | Manual keyframes, assisted aim/zoom snapping, or fully automatic orbit/static/flyby paths. |
| Framing | `dronecam.framing` | Project drones into the camera frame; score visibility, margins, fill, centring. |
| Simulation | `dronecam.simulation` | Step the timeline, score coverage, validate speed/climb/yaw/gimbal/zoom limits. |
| Export | `dronecam.export` | Mission JSON + Litchi-style CSV + KML flight path + camera command timeline. |

## Quick start

No installation needed (pure standard library). From the repo root:

```bash
# Run the whole pipeline on synthetic data and write mission files to ./out
python -m dronecam demo -o out

# Or step through it with the bundled example show:
python -m dronecam info     examples/sample_show.json
python -m dronecam plan     examples/sample_show.json -o path.json --mode orbit
python -m dronecam simulate  examples/sample_show.json path.json
python -m dronecam export    examples/sample_show.json path.json \
    --lat 1.2897 --lon 103.8501 --heading 0 -o out
```

`demo` prints a report like:

```
3. Simulated capture:
     Coverage score      :  99.3%
     Fully framed frames : 100.0%
     Visible drones      : mean 100.0%  min 100.0%
     Max speed           :   9.30 m/s
     Max climb/descent   :   3.02 m/s
     Max yaw rate        :    6.9 deg/s
     Constraint warnings : 0
```

## Importing a Blender show

Run [`tools/blender_export.py`](tools/blender_export.py) inside Blender to write
the show JSON. Put your drone objects in a collection (default name `Drones`):

```bash
blender show.blend --background --python tools/blender_export.py -- \
    --collection "Drones" --output show.json
```

The exporter samples every object's world-space position on every frame and
writes Blender's native Z-up coordinates. See `dronecam/show.py` for the JSON
schema (you can also hand-author it, or import a flat `frame,drone,x,y,z` CSV).

## Planning modes

* **`orbit`** — sweep an arc around the formation (the cinematic default).
* **`static`** — hold a fixed vantage and let the gimbal/zoom track the show.
* **`flyby`** — fly a straight chord past the show.

The automatic planner places the camera at the closest standoff the widest lens
allows (so the path stays near the show), aims at the formation centroid, and
picks a focal length per keyframe so the formation fills the frame. It shares
the drone's speed budget between following the show's drift and the chosen move,
**capping the orbit sweep / flyby travel so the camera never has to exceed its
max speed.** Camera altitude is floored so it never flies underground.

For hand-drawn paths, build `CameraPath` keyframes directly; set
`look_at_show=True` to auto-aim at the formation, or call
`CameraPath.refine_aim()` for the *assisted* workflow (keep your positions, snap
the aim and zoom onto the show).

## Coordinate / GPS model

The world frame is metric (x=East, y=North, z=Up). A `GeoOrigin`
(latitude, longitude, altitude, and the compass `heading` of local +y) anchors
it to the real venue exactly once, at export time. World yaw (CCW from East) is
converted to a compass heading (CW from North) for the mission file.

## Export artefacts

`export` writes four files to the output directory:

* `mission.json` — canonical mission: waypoints with GPS/alt/heading/gimbal/zoom
  and `start_recording` / `stop_recording` actions, plus the timeline.
* `mission_litchi.csv` — a widely-understood waypoint CSV for quick field use.
* `mission.kml` — the flight path, for visual inspection in Google Earth / GIS.
* `mission_camera_timeline.csv` — the per-waypoint camera command timeline.

## Project layout

```
dronecam/            the package
  geometry.py        vectors, camera basis, local<->GPS conversion
  show.py            show data model + Blender/CSV import
  camera.py          camera config + presets + pose
  framing.py         projection + framing/coverage scoring
  planner.py         camera path + manual/assisted/automatic planning
  simulation.py      timeline playback + coverage + limit checks
  export.py          mission/Litchi/KML/timeline export
  cli.py             command-line interface (python -m dronecam)
  samples.py         synthetic show generator (demos/tests)
tools/blender_export.py   run inside Blender to export a show
examples/sample_show.json a small ready-to-use show
tests/               unittest suite (python -m unittest discover -s tests)
```

## Running the tests

```bash
python -m unittest discover -s tests
```

## Roadmap (post-MVP)

Multi-camera support, an AI camera-operator that optimises shot selection, live
correction on site, automatic shot selection, and full-show coverage analytics.
