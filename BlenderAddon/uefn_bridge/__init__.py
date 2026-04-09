bl_info = {
    "name": "UEFN Blender Bridge",
    "author": "KiKoZl (Surprise Co.)",
    "version": (0, 5, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > UEFN",
    "description": "Live bridge from Blender to UEFN — FBX export, materials, textures, and live sync (beta)",
    "category": "Import-Export",
    "doc_url": "https://github.com/KiKoZl1/uefn-blender-bridge",
    "tracker_url": "https://github.com/KiKoZl1/uefn-blender-bridge/issues",
}

from . import bridge


def register():
    bridge.register()


def unregister():
    bridge.unregister()
