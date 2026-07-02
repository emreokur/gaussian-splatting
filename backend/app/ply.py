"""Writer for the INRIA 3D Gaussian Splatting .ply format (SH degree 0).

The format stores, per splat: position, normal (unused), DC spherical-harmonics
color coefficients, opacity as a pre-sigmoid logit, per-axis scale as log(scale),
and a rotation quaternion (w, x, y, z). Viewers such as gaussian-splats3d apply
sigmoid() to opacity and exp() to scales when loading.
"""

import numpy as np

SH_C0 = 0.28209479177387814

FIELDS = [
    "x", "y", "z",
    "nx", "ny", "nz",
    "f_dc_0", "f_dc_1", "f_dc_2",
    "opacity",
    "scale_0", "scale_1", "scale_2",
    "rot_0", "rot_1", "rot_2", "rot_3",
]


def write_gaussian_ply(path, positions, colors, scales, opacities, rotations):
    """Write a gaussian splat cloud to `path`.

    positions: (n, 3) float, world units
    colors:    (n, 3) float, linear RGB in [0, 1]
    scales:    (n, 3) float, gaussian sigma per axis in world units
    opacities: (n,)   float in (0, 1)
    rotations: (n, 4) float quaternion (w, x, y, z)
    """
    n = positions.shape[0]
    eps = 1e-6

    f_dc = (colors.astype(np.float32) - 0.5) / SH_C0
    alpha = np.clip(opacities.astype(np.float32), eps, 1.0 - eps)
    opacity_logit = np.log(alpha / (1.0 - alpha)).reshape(-1, 1)
    log_scales = np.log(np.maximum(scales.astype(np.float32), 1e-9))
    normals = np.zeros((n, 3), dtype=np.float32)

    data = np.hstack([
        positions.astype(np.float32),
        normals,
        f_dc,
        opacity_logit,
        log_scales,
        rotations.astype(np.float32),
    ])

    header_lines = ["ply", "format binary_little_endian 1.0", f"element vertex {n}"]
    header_lines += [f"property float {name}" for name in FIELDS]
    header_lines.append("end_header")

    with open(path, "wb") as f:
        f.write(("\n".join(header_lines) + "\n").encode("ascii"))
        f.write(np.ascontiguousarray(data, dtype="<f4").tobytes())

    return n
