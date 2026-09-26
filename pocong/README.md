# Pocong STL generator

Procedurally generates a **life-size pocong** (Indonesian shrouded-corpse
ghost) as a watertight binary STL — ready for 3D printing, CNC foam, or use as
a prop master.

A pocong is a body wrapped in a burial shroud (*kain kafan*), tied into a knot
above the head and at the feet. That shape is essentially a surface of
revolution, so instead of guessing at an AI image-to-3D conversion we revolve a
hand-tuned silhouette around the vertical axis. The result is a clean,
manifold, genus-0 shell (verified: every edge shared by exactly two triangles).

## Quick start

```bash
pip install numpy            # matplotlib only needed for --preview
python3 generate_pocong.py                       # -> pocong.stl (1650 mm tall)
python3 generate_pocong.py --preview preview.png # also render a PNG
```

## Options

| flag           | default | meaning                                            |
|----------------|---------|----------------------------------------------------|
| `--height`     | `1650`  | total height in **mm** (1650 = life size)          |
| `--width`      | `460`   | max body width / diameter in mm                    |
| `--segments`   | `160`   | radial segments (higher = smoother, bigger file)   |
| `--folds`      | `0.04`  | cloth-fold ripple amplitude (`0` = perfectly smooth)|
| `--fold-count` | `14`    | number of vertical folds around the shroud         |
| `--face`       | `0.12`  | forward face bulge in the head (`0` = none)         |
| `--preview`    | —       | also write a PNG preview render                     |
| `-o/--output`  | `pocong.stl` | output path                                    |

Examples:

```bash
# Smooth, no folds, no face — a clean abstract shroud
python3 generate_pocong.py --folds 0 --face 0 -o pocong_smooth.stl

# Tabletop 200 mm version with strong folds
python3 generate_pocong.py --height 200 --width 56 --folds 0.06 -o pocong_mini.stl
```

## Units & printing notes

* STL has no real units; this script writes **millimetres** (the common
  convention), so the default model imports at true life size in any slicer.
* At 1650 mm it is far larger than any consumer printer bed — slice it into
  sections (PrusaSlicer/Cura "Cut", or Meshmixer) and print in parts, or
  scale down. The mesh itself is a single sealed shell, so the slicer controls
  wall thickness and infill; print hollow (e.g. vase/spiralize mode or 2–3
  perimeters, 0–5% infill) to save material on big prints.
* The shell is watertight and outward-normaled, so no repair step is needed.

## How it works

1. `PROFILE` — `(height, radius)` control points describing the pocong
   silhouette from tied feet to top knot.
2. `catmull_rom` — smooths the profile through every control point.
3. `build_pocong` — revolves the profile into a triangle mesh, seals both
   tips with apex fans, and optionally adds cloth folds / a face bulge.
4. `write_binary_stl` — writes a binary STL with computed outward normals.
