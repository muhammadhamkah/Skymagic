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

## Blender add-on (plan inside Blender)

The easiest way to use this on a real show is the bundled Blender add-on. It
reads the drones straight from your scene, builds a **real animated camera you
can scrub and look through** to preview the footage, and exports the mission —
all from a sidebar panel.

**Install**

```bash
python build_addon.py            # writes dist/drone_camera_planner.zip
```

In Blender: *Edit > Preferences > Add-ons > Install...*, pick the zip, enable
**Drone Camera Planner**. A **Drone Cam** tab appears in the 3D viewport sidebar
(press `N`). The add-on vendors the dependency-free `dronecam` package, so no
`pip install` inside Blender is needed.

**Use**

1. **Show drones** — choose how to find the drones. For shows whose objects are
   named like `UAV_000329`, use *Name prefix* `UAV_`. (Or pick a collection, or
   just select the drone objects.)
2. **Frame range** — *Use scene frame range* (or set your own). Raise *Sample
   step* for very long shows — at step 25 a 15,000-frame show is ~600 samples,
   which plans in a couple of seconds.
3. **Camera & shot** — pick a camera preset and a mode (`orbit`/`static`/`flyby`)
   and nudge *elevation*, *azimuth*, *orbit sweep* and *frame fill*.
4. **Generate Camera Path** — creates/updates a `DroneCam` object animated along
   the path and makes it the active camera. Press `Numpad 0` to look through it
   and scrub the timeline to preview. The coverage report shows up in the panel.
5. **Venue origin** — enter the venue latitude/longitude (and the compass
   heading of the scene's +Y axis), then **Export Mission**.

### Scene-by-scene planning (drawing shots)

Real shows are filmed scene by scene, repositioning during transitions. The
**Scenes / shots** section lets you do exactly that:

1. **Add a scene** (the `+` button). Set its **Start/End** frames — scrub the
   timeline and use the ⤓/⤒ buttons to grab the playhead.
2. **Define the camera move**, either way:
   * **Draw it** — click **Draw Path** (pencil button). It creates a curve and
     drops you into Blender's curve *Draw tool* so you can sketch the flight in
     the viewport (draw the ground track from Top view, then raise it in Front
     view). The drawn curve is sampled as the path.
   * **Or drop waypoints** — position the 3D cursor (Shift + Right-click) and
     click **Add Waypoint at Cursor**. One waypoint = a fixed vantage that
     tracks the show; several = a rough path **Catmull-Rom smoothed** into a
     glide through your points.
3. Add more scenes the same way. The camera always aims at the drone cloud and
   picks its zoom to fill the frame.
4. **Generate Camera Path** stitches them together: each scene plays its move,
   and the **gaps between scenes become automatic eased repositioning moves** so
   the drone is in place when the next scene starts. *Smoothness (kf/s)* controls
   how finely the path is baked; *Standby (s)* makes the drone arrive at the next
   vantage early and hold so it is settled before the scene begins; *Record
   during transitions* toggles whether the camera keeps rolling while it moves.

Generating is non-destructive — it only builds the preview `DroneCam` and path
line, so iterate freely (Numpad 0 to look through it, play the timeline to
preview the move). Nothing leaves Blender until **Export Mission**.

The coverage report still validates everything — if you space waypoints too far
apart for the time available, the **speed warnings** tell you to either spread
the move over more frames or move the points closer. With no scenes defined, the
planner falls back to the single automatic *Auto shot* (orbit/static/flyby).

### Manual / live recording (fly the camera yourself)

Prefer to pilot the camera by hand while watching the show? The **Manual / live
record** section lets you fly it in the viewport and record the move:

1. Click **Live Record (fly the cam)**. It creates/locks the `DroneCam` to your
   viewport view, switches to camera view, and turns on auto-keyframing.
2. Press **Play**, then **fly** — use Fly mode (`Shift + \``) like a game, or
   orbit/pan/zoom. As you move, the camera is keyframed, so you keep the show in
   frame exactly how you want.
3. Click **Stop Recording** when done.
4. With **Use my DroneCam animation** ticked, **Generate** runs the coverage and
   clearance checks on what you flew (and draws the path line), and **Export
   Mission** turns your flown camera into the waypoint mission.

The same path applies to a hand-keyframed camera: animate `DroneCam` however you
like in Blender, tick *Use my DroneCam animation*, and export it with full
speed/clearance validation.

### Seeing the path & staying clear of the show

The camera drone must never fly into the show, so on every **Generate**:

* a persistent **`DroneCamPath`** curve is drawn through the whole trajectory
  (start → end) so you can see at a glance where the camera goes relative to the
  formations, and
* the simulation reports the **minimum clearance** — the closest the camera ever
  gets to any show drone — and raises a `clearance` warning if it breaches the
  **Safety clearance (m)** you set. Bump the safety radius to your operational
  separation; if you see clearance warnings, move the offending scene's waypoints
  further out from the formation.

## Importing a Blender show (headless / CLI)

Prefer the CLI? Run [`tools/blender_export.py`](tools/blender_export.py) inside
Blender to write the show JSON, then use the `dronecam` commands. Select drones
by name prefix (matches `UAV_…` objects), by collection, or by selection:

```bash
# 500-drone festival show named UAV_*, sampling every 25th frame:
blender wilderness_500_2023_flyable.blend --background \
    --python tools/blender_export.py -- \
    --prefix "UAV_" --step 25 --output wilderness.json

python -m dronecam plan     wilderness.json -o path.json --mode orbit --preset long-lens
python -m dronecam simulate  wilderness.json path.json --preset long-lens
python -m dronecam export    wilderness.json path.json --lat <LAT> --lon <LON> -o out
```

The exporter samples every object's world-space position and writes Blender's
native Z-up coordinates. See `dronecam/show.py` for the JSON schema (you can also
hand-author it, or import a flat `frame,drone,x,y,z` CSV).

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
tools/blender_export.py   run inside Blender to export a show (headless/CLI)
blender_addon/            the "Drone Camera Planner" Blender add-on
  drone_camera_planner/
    __init__.py           bl_info, properties, operators, sidebar panel
    bridge.py             scene<->dronecam glue + animated-camera builder
build_addon.py            bundles the add-on (+ vendored dronecam) into dist/*.zip
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
