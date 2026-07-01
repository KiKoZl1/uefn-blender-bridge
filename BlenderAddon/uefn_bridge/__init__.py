bl_info = {
    "name": "UEFN Blender Bridge",
    "author": "KiKoZl (Surprise Co.)",
    "version": (1, 0, 2),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > UEFN",
    "description": "Two-way live bridge between Blender and UEFN — meshes, PBR materials, LOD, instancing, and bidirectional sync",
    "category": "Import-Export",
    "doc_url": "https://github.com/KiKoZl1/uefn-blender-bridge",
    "tracker_url": "https://github.com/KiKoZl1/uefn-blender-bridge/issues",
}

from . import bridge


def register():
    bridge.register()


def unregister():
    bridge.unregister()
