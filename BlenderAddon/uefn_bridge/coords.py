"""Blender <-> UEFN coordinate / rotation convention.

Pure module — NO bpy/unreal imports — so it is unit-testable standalone and is the
single source of truth for the convention used on both the outbound (Blender->UEFN)
and inbound (UEFN->Blender) paths.

Blender: X=right, Y=forward, Z=up (right-handed, meters).
UEFN:    X=forward, Y=right, Z=up (left-handed, centimeters).

The FBX pipeline reorients mesh vertices; world transforms only need the handedness
flip (negate Y) plus the meters->centimeters scale.

    Position:  UE.X = BL.X*100,  UE.Y = -BL.Y*100,  UE.Z = BL.Z*100
    Rotation:  Pitch = -deg(ry), Yaw = -deg(rz),    Roll = +deg(rx)
"""

import math

SCALE = 100.0  # Blender meters -> UEFN centimeters


def loc_bl_to_ue(x, y, z):
    """Blender world location (m) -> UEFN [x, y, z] (cm)."""
    return [x * SCALE, -y * SCALE, z * SCALE]


def loc_ue_to_bl(x, y, z):
    """UEFN location (cm) -> Blender world [x, y, z] (m)."""
    return [x / SCALE, -y / SCALE, z / SCALE]


def rot_bl_to_ue(rx, ry, rz):
    """Blender world euler (radians, XYZ) -> UEFN [pitch, yaw, roll] (degrees)."""
    return [-math.degrees(ry), -math.degrees(rz), math.degrees(rx)]


def rot_ue_to_bl(pitch, yaw, roll):
    """UEFN [pitch, yaw, roll] (degrees) -> Blender world euler (radians, XYZ) [rx, ry, rz]."""
    return [math.radians(roll), -math.radians(pitch), -math.radians(yaw)]
