"""Unit tests for the pure Blender <-> UEFN coordinate convention.

Runs without Blender/pytest:  python tests/test_coords.py
Also pytest-discoverable:      pytest tests/
"""

import math
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "BlenderAddon", "uefn_bridge")
)

import coords  # noqa: E402


def test_loc_convention():
    assert coords.loc_bl_to_ue(1, 2, 3) == [100.0, -200.0, 300.0]
    assert coords.loc_ue_to_bl(100.0, -200.0, 300.0) == [1.0, 2.0, 3.0]


def test_loc_roundtrip():
    for p in [(1, 2, 3), (-5, 0.5, 12.34), (0, 0, 0), (-0.001, 999, -42)]:
        bl = coords.loc_ue_to_bl(*coords.loc_bl_to_ue(*p))
        assert all(abs(a - b) < 1e-9 for a, b in zip(p, bl)), p


def test_rot_roundtrip():
    for r in [(0.1, 0.2, 0.3), (-1.0, 0.5, 2.0), (0, 0, 0), (math.pi, -math.pi / 2, 1.0)]:
        bl = coords.rot_ue_to_bl(*coords.rot_bl_to_ue(*r))
        assert all(abs(a - b) < 1e-9 for a, b in zip(r, bl)), r


def test_rot_known_value():
    # 90deg of Blender world Z (yaw axis) -> UEFN Yaw = -90
    ue = coords.rot_bl_to_ue(0.0, 0.0, math.radians(90))
    assert abs(ue[1] - (-90.0)) < 1e-9


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
