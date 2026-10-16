#!/usr/bin/env python3
"""
generate_pocong.py — Parametric 3D model generator for a *pocong*
(the Indonesian shrouded-corpse ghost), output as a watertight binary STL
suitable for 3D printing / CNC / props.

A pocong is a body wrapped tightly in a burial shroud (kain kafan), tied
into a knot above the head and at the feet. That silhouette is essentially a
surface of revolution — a bulbous body, a pinch at the neck, a rounded head,
and a tapering top knot — so we build it by revolving a tuned radius profile
around the vertical axis. This yields a clean, watertight mesh (no holes,
consistent winding) which is exactly what slicers want.

Extras:
  * subtle vertical "cloth fold" ripples around the shroud (--folds)
  * a gentle forward face bulge in the head region (--face)
  * pure-numpy STL writer (no heavy mesh deps)

Usage:
  python3 generate_pocong.py --height 1650 --width 460 -o pocong.stl
  python3 generate_pocong.py --preview pocong_preview.png

All linear units are millimetres (the de-facto STL convention), so the
default 1650 mm tall model is genuinely life-size.
"""

from __future__ import annotations

import argparse
import math
import struct

import numpy as np


# ---------------------------------------------------------------------------
# Silhouette of a pocong, as (t, r) control points where
#   t = normalized height, 0.0 = feet (bottom) .. 1.0 = top of the head knot
#   r = radius as a fraction of the maximum (widest) radius, 0.0 .. 1.0
# Hand-tuned to read as: tied feet -> wide wrapped body -> shoulders ->
# neck pinch -> rounded head -> taper -> top knot -> tied tip.
# ---------------------------------------------------------------------------
PROFILE = [
    (0.000, 0.000),   # very tip of the bottom (feet) tie — sealed point
    (0.012, 0.18),    # the little knot ball at the feet
    (0.030, 0.10),    # pinch just above the feet tie
    (0.060, 0.34),
    (0.120, 0.62),
    (0.220, 0.86),
    (0.380, 1.000),   # belly — widest part of the wrapped body
    (0.520, 0.97),
    (0.620, 0.88),    # chest
    (0.690, 0.70),    # shoulders start narrowing
    (0.730, 0.55),
    (0.755, 0.50),    # neck tie — the pinch under the head
    (0.785, 0.58),
    (0.840, 0.73),    # face / head, widest of the head
    (0.885, 0.70),
    (0.920, 0.52),
    (0.945, 0.30),    # taper above the head
    (0.965, 0.16),
    (0.980, 0.22),    # the top knot ball (the iconic tied top)
    (0.992, 0.12),
    (1.000, 0.000),   # sealed top tip
]


def catmull_rom(points: np.ndarray, samples_per_seg: int = 24) -> np.ndarray:
    """Smooth a (t, r) control polyline with a Catmull-Rom spline.

    Keeps the curve passing through every control point (so the silhouette
    stays faithful) while rounding the body, head and knots. Endpoints are
    duplicated so the curve reaches the sealed tips. r is clamped >= 0.
    """
    p = np.asarray(points, dtype=float)
    ext = np.vstack([p[0], p, p[-1]])  # phantom endpoints
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for s in range(samples_per_seg):
            u = s / samples_per_seg
            u2, u3 = u * u, u * u * u
            # Catmull-Rom basis (tension 0.5)
            point = 0.5 * (
                (2 * p1)
                + (-p0 + p2) * u
                + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2
                + (-p0 + 3 * p1 - 3 * p2 + p3) * u3
            )
            out.append(point)
    out.append(ext[-2])
    out = np.array(out)
    # de-duplicate near-identical t values and enforce monotonic t
    keep = np.concatenate([[True], np.diff(out[:, 0]) > 1e-6])
    out = out[keep]
    out[:, 1] = np.clip(out[:, 1], 0.0, None)
    return out


def build_pocong(
    height: float,
    max_radius: float,
    n_theta: int = 160,
    fold_amp: float = 0.0,
    fold_count: int = 14,
    face_bulge: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Revolve the profile into a watertight triangle mesh.

    Returns (vertices [N,3], faces [M,3] int).
    """
    prof = catmull_rom(PROFILE)
    t = prof[:, 0]
    r = prof[:, 1] * max_radius
    z = t * height
    n_rings = len(t)

    theta = np.linspace(0.0, 2.0 * math.pi, n_theta, endpoint=False)
    cos_t, sin_t = np.cos(theta), np.sin(theta)

    verts = np.zeros((n_rings * n_theta, 3), dtype=float)
    for i in range(n_rings):
        rad = np.full(n_theta, r[i])

        # vertical cloth folds: gentle ripple in radius around the body,
        # faded out at the very ends so the tied tips stay clean.
        if fold_amp > 0.0:
            end_fade = math.sin(math.pi * t[i]) ** 0.5
            rad = rad * (1.0 + fold_amp * end_fade * np.cos(fold_count * theta))

        ring = np.column_stack([rad * cos_t, rad * sin_t, np.full(n_theta, z[i])])

        # forward face bulge: push the +X-facing part of the head region out
        # a little to suggest a face pressing against the shroud.
        if face_bulge > 0.0 and 0.78 <= t[i] <= 0.90:
            head_w = math.sin((t[i] - 0.78) / (0.90 - 0.78) * math.pi)  # 0..1..0
            # angular window centred on +X (theta = 0)
            ang = (theta + math.pi) % (2 * math.pi) - math.pi  # -pi..pi
            face_win = np.clip(np.cos(ang), 0.0, None) ** 2
            push = face_bulge * max_radius * head_w * face_win
            ring[:, 0] += push * cos_t
            ring[:, 1] += push * sin_t

        verts[i * n_theta:(i + 1) * n_theta] = ring

    # collapse the two end rings (radius ~0) onto single apex points so the
    # surface seals cleanly without slivers.
    bottom_apex = len(verts)
    top_apex = bottom_apex + 1
    verts = np.vstack([verts, [0.0, 0.0, z[0]], [0.0, 0.0, z[-1]]])

    faces = []

    def idx(ring, j):
        return ring * n_theta + (j % n_theta)

    # side quads between consecutive rings (skip the degenerate first/last
    # ring -> handled by the apex fans below)
    for ring in range(1, n_rings - 2):
        for j in range(n_theta):
            a = idx(ring, j)
            b = idx(ring, j + 1)
            c = idx(ring + 1, j + 1)
            d = idx(ring + 1, j)
            faces.append((a, b, c))
            faces.append((a, c, d))

    # bottom cap: apex -> first real ring (ring index 1)
    for j in range(n_theta):
        faces.append((bottom_apex, idx(1, j + 1), idx(1, j)))
    # top cap: apex -> last real ring (ring index n_rings-2)
    last = n_rings - 2
    for j in range(n_theta):
        faces.append((top_apex, idx(last, j), idx(last, j + 1)))

    return verts, np.asarray(faces, dtype=np.int64)


def write_binary_stl(path: str, verts: np.ndarray, faces: np.ndarray) -> None:
    """Write a binary STL with outward-facing normals."""
    tris = verts[faces]                       # [M,3,3]
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    normals = np.cross(v1 - v0, v2 - v0)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    normals = normals / lengths

    with open(path, "wb") as f:
        f.write(b"pocong - parametric shroud ghost".ljust(80, b" "))
        f.write(struct.pack("<I", len(faces)))
        for n, tri in zip(normals, tris):
            f.write(struct.pack("<3f", *n))
            for v in tri:
                f.write(struct.pack("<3f", *v))
            f.write(struct.pack("<H", 0))


def save_preview(path: str, verts: np.ndarray, faces: np.ndarray) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    # subsample faces for a quick render
    step = max(1, len(faces) // 6000)
    f = faces[::step]
    fig = plt.figure(figsize=(4, 8))
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(verts[f], facecolor="#e8e8ea", edgecolor="none", alpha=1.0)
    coll.set_zsort("max")
    ax.add_collection3d(coll)
    ctr = verts.mean(axis=0)
    half = np.ptp(verts[:, :2], axis=0).max() / 2 * 1.1
    h = verts[:, 2].max() - verts[:, 2].min()
    ax.set_xlim(ctr[0] - half, ctr[0] + half)
    ax.set_ylim(ctr[1] - half, ctr[1] + half)
    ax.set_zlim(verts[:, 2].min(), verts[:, 2].max())
    # true proportions: box aspect matches real width:width:height
    ax.set_box_aspect((2 * half, 2 * half, h))
    ax.view_init(elev=6, azim=-70)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a pocong STL (life-size by default).")
    ap.add_argument("-o", "--output", default="pocong.stl", help="output STL path")
    ap.add_argument("--height", type=float, default=1650.0, help="total height in mm (default 1650 = life size)")
    ap.add_argument("--width", type=float, default=460.0, help="max body width (diameter) in mm")
    ap.add_argument("--segments", type=int, default=160, help="radial segments (smoothness)")
    ap.add_argument("--folds", type=float, default=0.04, help="cloth fold ripple amplitude (0 = smooth)")
    ap.add_argument("--fold-count", type=int, default=14, help="number of vertical folds")
    ap.add_argument("--face", type=float, default=0.12, help="forward face bulge (0 = none)")
    ap.add_argument("--preview", metavar="PNG", help="also write a PNG preview render")
    args = ap.parse_args()

    verts, faces = build_pocong(
        height=args.height,
        max_radius=args.width / 2.0,
        n_theta=args.segments,
        fold_amp=args.folds,
        fold_count=args.fold_count,
        face_bulge=args.face,
    )
    write_binary_stl(args.output, verts, faces)
    print(f"wrote {args.output}: {len(verts)} verts, {len(faces)} triangles")
    print(f"  bounding box: {args.width:.0f} x {args.width:.0f} x {args.height:.0f} mm")

    if args.preview:
        save_preview(args.preview, verts, faces)
        print(f"wrote preview {args.preview}")


if __name__ == "__main__":
    main()
