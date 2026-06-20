"""UEFN Blender Bridge — UEFN Side.

Architecture:
  - HTTP server receives commands from Blender addon
  - FBX import via AssetImportTask + FbxImportUI
  - PBR material creation via MaterialEditingLibrary
  - Tkinter dashboard for status and configuration
  - Tick-integrated command execution (thread-safe)

Run: UEFN > Tools > Execute Python Script > this file

github.com/KiKoZl1 | Surprise Co. (surpriseugc.com)
"""

import unreal
import hashlib
import io
import json
import math
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional

# ============================================================
# CONFIG
# ============================================================

PLUGIN_VERSION = "0.5.0-beta"
DEFAULT_PORT = 8790
MAX_PORT = 8795
TICK_BATCH = 5
HTTP_TIMEOUT = 120.0
POLL_INTERVAL = 0.02
STALE_SEC = 120.0
ACTOR_PREFIX = "BB_"
LIVE_POLL_INTERVAL = 0.4   # seconds between UEFN->Blender transform polls when Live Sync is on
# Commands that write BB_ actor transforms from Blender data — after one runs, the push
# baseline is refreshed so the live poll doesn't echo Blender's own writes straight back.
INBOUND_WRITE_CMDS = frozenset(
    ("import_scene", "import_baked", "add_objects", "update_objects", "update_transforms"))

# ============================================================
# GLOBALS
# ============================================================

_http_server = None
_http_thread = None
_bound_port = 0
_command_queue = queue.Queue()
_responses = {}
_responses_lock = threading.Lock()
_req_counter = 0

_project_path = ""
_bridge_project = ""       # project name from Blender (folder under BlenderBridge/)
_blender_server_port = 0   # Blender's HTTP server port for bidirectional sync
_last_push_snapshot = {}   # {obj_name: "loc|rot|scale" hash} for diff-based push
_live_sync_active = False  # True while Blender Live Sync is on -> UEFN polls + pushes changes
_eal_cache = None          # cached EditorAssetSubsystem (modern) or EditorAssetLibrary fallback
_import_scale = 1.0
_auto_collision = True

_blender_info = {}
_imported_scenes = {}
_last_import = 0.0
_total_imports = 0
_log_ring = []
_gui = None

# ============================================================
# CHANNEL MAP
# ============================================================

CHANNEL_MAP = {
    "basecolor": "BaseColor", "base_color": "BaseColor", "diffuse": "BaseColor",
    "albedo": "BaseColor", "color": "BaseColor",
    "normal": "Normal", "normalmap": "Normal", "normal_map": "Normal",
    "roughness": "Roughness", "metallic": "Metallic", "metalness": "Metallic",
    "height": "Height", "displacement": "Height",
    "ambientocclusion": "AO", "ambient_occlusion": "AO", "ao": "AO",
    "emissive": "Emissive", "emission": "Emissive",
    "opacity": "Opacity", "specular": "Specular",
}

MAT_PROP = {
    "BaseColor": "MP_BASE_COLOR", "Normal": "MP_NORMAL",
    "Roughness": "MP_ROUGHNESS", "Metallic": "MP_METALLIC",
    "Emissive": "MP_EMISSIVE_COLOR", "Opacity": "MP_OPACITY",
    "Specular": "MP_SPECULAR", "AO": "MP_AMBIENT_OCCLUSION",
}

CH_DISPLAY = {
    "BaseColor": "Base Color", "Normal": "Normal", "Roughness": "Roughness",
    "Metallic": "Metallic", "Height": "Height", "AO": "AO",
    "Emissive": "Emissive", "Opacity": "Opacity", "Specular": "Specular",
}

CH_COLORS = {
    "BaseColor": "#e07840", "Normal": "#7b7de0", "Roughness": "#5a9a5a",
    "Metallic": "#a8a8a8", "Height": "#8a7a5e", "AO": "#606060",
    "Emissive": "#d0d040", "Opacity": "#4090d0", "Specular": "#d04040",
}

# ============================================================
# HELPERS
# ============================================================

def _log(msg, level="info"):
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _log_ring.append(entry)
    if len(_log_ring) > 300:
        _log_ring.pop(0)
    tag = "[BlenderBridge]"
    if level == "error":
        unreal.log_error(f"{tag} {msg}")
    elif level == "warning":
        unreal.log_warning(f"{tag} {msg}")
    else:
        unreal.log(f"{tag} {msg}")
    if _gui:
        _gui._on_log(entry, level)


def _base_dir(name=""):
    """Root directory for current bridge project."""
    base = _project_path.strip("/") if _project_path else "Game"
    pname = _bridge_project or "Default"
    root = f"/{base}/BlenderBridge/{pname}"
    return f"{root}/{name}" if name else root


def _mesh_dir(scene_name=""):
    """Directory for mesh assets."""
    root = _base_dir("Meshes")
    return f"{root}/{scene_name}" if scene_name else root


def _mat_dir(scene_name=""):
    """Directory for material assets."""
    root = _base_dir("Materials")
    return f"{root}/{scene_name}" if scene_name else root


def _tex_dir(scene_name=""):
    """Directory for texture assets."""
    root = _base_dir("Textures")
    return f"{root}/{scene_name}" if scene_name else root


def _sanitize_seg(s):
    """UE asset-path segment: only [A-Za-z0-9_-]; everything else (spaces, dots, etc.) -> '_'.
    A space or dot in a folder name makes rename_asset fail silently."""
    return "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in s)


def _collection_folder(root, collection):
    """Asset folder for a Blender collection under a type root (F-16). Mirrors the Blender
    collection hierarchy (no scene wrapper); empty collection -> the type root. Each segment is
    sanitized for the Content Browser (the Outliner folder keeps the original name with spaces)."""
    c = (collection or "").strip("/")
    if not c:
        return root
    segs = [_sanitize_seg(s) for s in c.split("/") if s]
    segs = [s for s in segs if s]
    return root + "/" + "/".join(segs) if segs else root


def _asset_lib():
    """Asset ops via EditorAssetSubsystem (modern) when available, else the deprecated-but-
    working EditorAssetLibrary. Both expose the same method names (does_asset_exist, load_asset,
    save_asset, delete_asset, list_assets, rename_asset, find_asset_data, does/delete_directory),
    so call sites are identical. Safe on restrictive builds — falls back instead of breaking."""
    global _eal_cache
    if _eal_cache is None:
        try:
            _eal_cache = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        except Exception:
            _eal_cache = None
        if _eal_cache is None:
            _eal_cache = unreal.EditorAssetLibrary
    return _eal_cache


def _detect_project_path():
    global _project_path
    try:
        world = unreal.get_editor_subsystem(
            unreal.UnrealEditorSubsystem).get_editor_world()
        wp = world.get_path_name()
        _project_path = "/" + wp.split("/")[1]
        _log(f"Project path: {_project_path}")
    except Exception:
        _project_path = "/Game"
        _log("Could not detect project path, using /Game", "warning")


def _detect_channel(filename):
    n = os.path.splitext(filename)[0].lower()
    for key, ch in CHANNEL_MAP.items():
        if n.endswith(key) or n.endswith("_" + key):
            return ch
    return None


def _get_set_name(filename):
    n = os.path.splitext(filename)[0]
    nl = n.lower()
    for key in CHANNEL_MAP:
        if nl.endswith("_" + key):
            return n[:-(len(key) + 1)]
        if nl.endswith(key):
            return n[:-len(key)].rstrip("_")
    return n


def _serialize(obj):
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if hasattr(obj, "get_path_name"):
        return str(obj.get_path_name())
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def _asset_class(path):
    try:
        ad = _asset_lib().find_asset_data(path)
        return str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") else str(getattr(ad, "asset_class", ""))
    except Exception:
        return ""

# ============================================================
# TEXTURE IMPORT
# ============================================================

def _import_tex(fp, dest, name):
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", fp)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("destination_name", name)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    exp = f"{dest}/{name}"
    if _asset_lib().does_asset_exist(exp):
        return exp
    try:
        ps = task.get_editor_property("imported_object_paths")
        if ps and len(ps) > 0:
            return str(ps[0])
    except Exception:
        pass
    return None


def _config_tex(path, ch):
    tex = _asset_lib().load_asset(path)
    if not tex:
        return
    try:
        if ch == "Normal":
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
            tex.set_editor_property("srgb", False)
        elif ch in ("Roughness", "Metallic", "AO", "Height", "Opacity", "Specular"):
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
            tex.set_editor_property("srgb", False)
        elif ch in ("BaseColor", "Emissive"):
            tex.set_editor_property("srgb", True)
        _asset_lib().save_asset(path)
    except Exception:
        pass

# ============================================================
# FBX IMPORT
# ============================================================

def _import_fbx(fbx_path, dest_path, combine=False):
    if not os.path.exists(fbx_path):
        _log(f"FBX not found: {fbx_path}", "error")
        return []

    _log(f"Importing FBX: {os.path.basename(fbx_path)} -> {dest_path}")

    task = unreal.AssetImportTask()
    task.set_editor_property("filename", fbx_path)
    task.set_editor_property("destination_path", dest_path)
    task.set_editor_property("destination_name", "")
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)

    options = unreal.FbxImportUI()
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_textures", True)
    options.set_editor_property("import_materials", True)
    options.set_editor_property("import_as_skeletal", False)
    options.set_editor_property("import_animations", False)
    options.set_editor_property("automated_import_should_detect_type", False)
    options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)

    sm = options.get_editor_property("static_mesh_import_data")
    sm.set_editor_property("combine_meshes", combine)
    sm.set_editor_property("auto_generate_collision", _auto_collision)
    # No conversions on import — the FBX is already baked to cm + Z-up on export, so this
    # works whether UEFN honors these flags (legacy FBX) or ignores them (Interchange).
    sm.set_editor_property("convert_scene", False)
    sm.set_editor_property("convert_scene_unit", False)

    task.set_editor_property("options", options)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    # Collect imported assets
    imported = []
    try:
        paths = task.get_editor_property("imported_object_paths")
        if paths:
            imported = [str(p) for p in paths]
    except Exception:
        pass

    if not imported:
        try:
            reg = unreal.AssetRegistryHelpers.get_asset_registry()
            for ad in reg.get_assets_by_path(dest_path, recursive=True):
                cls = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") else ""
                if "StaticMesh" in cls:
                    imported.append(str(ad.get_asset().get_path_name()))
        except Exception:
            pass

    _log(f"Imported {len(imported)} asset(s)")
    return imported

# ============================================================
# MESH PLACEMENT
# ============================================================

def _spawn_at_camera():
    try:
        loc, rot = unreal.get_editor_subsystem(
            unreal.UnrealEditorSubsystem).get_level_viewport_camera_info()
        yr = math.radians(rot.yaw)
        pr = math.radians(rot.pitch)
        return unreal.Vector(
            loc.x + math.cos(pr) * math.cos(yr) * 500,
            loc.y + math.cos(pr) * math.sin(yr) * 500,
            loc.z + math.sin(pr) * 500
        )
    except Exception:
        return unreal.Vector(0, 0, 0)


def _find_actor_by_label(asub, label):
    for a in asub.get_all_level_actors():
        try:
            if a.get_actor_label() == label:
                return a
        except Exception:
            pass
    return None


def _find_actor_by_guid(asub, guid):
    needle = f"BB_GUID:{guid}"
    for a in asub.get_all_level_actors():
        try:
            if any(str(t) == needle for t in a.tags):
                return a
        except Exception:
            pass
    return None


def _ensure_sm_name(asset, base, dest_folder=None):
    """Rename/move an imported StaticMesh to <dest_folder>/SM_<base> (F-43 naming + F-16
    collection subfolder). dest_folder defaults to the asset's current folder. Reuses an
    existing asset at the destination (mesh dedup — F-44). Falls back safely."""
    try:
        pkg = asset.get_path_name().split(".")[0]
        cur_folder, cur = pkg.rsplit("/", 1)
        folder = dest_folder or cur_folder
        new_pkg = f"{folder}/SM_{base}"
        if new_pkg == pkg:
            return asset
        # Already named at destination (prior send) — reuse it, drop the staging duplicate.
        if _asset_lib().does_asset_exist(new_pkg):
            if pkg != new_pkg:
                try:
                    _asset_lib().delete_asset(pkg)
                except Exception:
                    pass
            return _asset_lib().load_asset(new_pkg) or asset
        ok = False
        try:
            ok = _asset_lib().rename_asset(pkg, new_pkg)
        except Exception as e:
            _log(f"  SM_ rename error {cur} -> {new_pkg}: {e}", "warning")
        if ok:
            moved = _asset_lib().load_asset(new_pkg)
            if moved:
                _log(f"  named SM_{base} @ {folder}")
                return moved
        _log(f"  SM_ rename FAILED: {cur} -> {new_pkg}", "warning")
    except Exception as e:
        _log(f"  SM_ route error for {base}: {e}", "warning")
    return asset


def _spawn_or_reuse(asub, asset, label, guid):
    """Reuse an existing bridge actor (by GUID, then label) or spawn a new one — idempotent (B3).
    Points it at `asset` and stamps the label + GUID tag. Returns the actor (or None on failure)."""
    existing = (_find_actor_by_guid(asub, guid) if guid else None) \
        or _find_actor_by_label(asub, label)
    if existing:
        comp = existing.static_mesh_component
        if comp:
            comp.set_static_mesh(asset)
        actor = existing
    else:
        actor = asub.spawn_actor_from_object(asset, unreal.Vector(0, 0, 0))
        if not actor:
            _log(f"  Spawn failed: {label}", "warning")
            return None
    actor.set_actor_label(label)
    if guid:
        actor.tags = [unreal.Name(f"BB_GUID:{guid}")]
    return actor


def _apply_obj_transform(actor, od):
    """Apply the per-object WORLD transform Blender sent (already in UE coords) + Outliner folder.
    Placement mirrors the Blender position — never a camera offset."""
    loc = od.get("location", [0, 0, 0])
    rot = od.get("rotation", [0, 0, 0])   # [pitch, yaw, roll]
    scale = od.get("scale", [1, 1, 1])
    collection = od.get("collection", "")
    actor.set_actor_location(unreal.Vector(loc[0], loc[1], loc[2]), False, False)
    actor.set_actor_rotation(unreal.Rotator(pitch=rot[0], yaw=rot[1], roll=rot[2]), False)
    actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
    actor.set_folder_path(f"/BlenderBridge/{collection}" if collection else "/BlenderBridge")


def _place_meshes(asset_paths, object_data=None, scene_name="Scene"):
    """Place actors. OBJECT-DRIVEN: each object references a (possibly shared) StaticMesh via its
    'mesh' field — N Blender objects sharing one mesh datablock spawn N actors off ONE imported
    mesh (instancing — F-44). Falls back to one-actor-per-mesh when no object data is given."""
    asub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = []

    # Load imported StaticMeshes, keyed by FBX-style source name (dots->underscores).
    mesh_by_name = {}
    for path in asset_paths:
        try:
            asset = unreal.load_asset(str(path))
            if not asset:
                clean = str(path).split(" ")[-1] if " " in str(path) else str(path)
                asset = unreal.load_asset(clean)
            if not asset or not isinstance(asset, unreal.StaticMesh):
                continue
            mesh_by_name[str(path).split("/")[-1].split(".")[0]] = asset
        except Exception as e:
            _log(f"  Load error: {path}: {e}", "warning")

    if not mesh_by_name:
        _log("  No StaticMeshes to place", "warning")
        return actors

    def _resolve(key):
        k = (key or "").replace(".", "_")
        if k in mesh_by_name:
            return k, mesh_by_name[k]
        if len(mesh_by_name) == 1:   # combined/single-mesh export fallback
            return next(iter(mesh_by_name.items()))
        return None, None

    if not object_data:
        # Legacy fallback: one actor per imported mesh.
        for name, asset in list(mesh_by_name.items()):
            asset = _ensure_sm_name(asset, name, _mesh_dir())
            actor = _spawn_or_reuse(asub, asset, f"{ACTOR_PREFIX}{name}", "")
            if actor:
                actor.set_folder_path("/BlenderBridge")
                actors.append(actor)
        _log(f"Placed {len(actors)} actor(s)")
        return actors

    renamed = {}  # mesh key -> StaticMesh after SM_/collection rename (once per shared mesh)
    for od in object_data:
        try:
            bname = od.get("name", "")
            guid = od.get("guid", "")
            collection = od.get("collection", "")
            mesh_key = od.get("mesh", bname)   # representative name (instancing)
            label = f"{ACTOR_PREFIX}{bname}"

            rk, asset = _resolve(mesh_key)
            if not asset:
                _log(f"  No mesh '{mesh_key}' for object '{bname}'", "warning")
                continue

            # Name/move the shared StaticMesh ONCE: <Meshes>/<collection>/SM_<rep> (F-16/F-43).
            if rk in renamed:
                asset = renamed[rk]
            else:
                asset = _ensure_sm_name(asset, mesh_key.replace(".", "_"),
                                        _collection_folder(_mesh_dir(), collection))
                renamed[rk] = asset
                mesh_by_name[rk] = asset

            actor = _spawn_or_reuse(asub, asset, label, guid)
            if not actor:
                continue
            _apply_obj_transform(actor, od)
            actors.append(actor)
            _log(f"Placed: {label} (mesh: {mesh_key})")
        except Exception as e:
            _log(f"Place error: {e}", "warning")

    _log(f"Placed {len(actors)} actor(s)")
    return actors

# ============================================================
# CLEANUP
# ============================================================

def _cleanup_actors(names=None):
    asub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    to_remove = []
    for actor in asub.get_all_level_actors():
        label = actor.get_actor_label()
        if not label or not label.startswith(ACTOR_PREFIX):
            continue
        if names:
            obj_name = label[len(ACTOR_PREFIX):]
            if obj_name in names:
                to_remove.append(actor)
        else:
            to_remove.append(actor)

    if to_remove:
        asub.destroy_actors(to_remove)
        _log(f"Cleaned {len(to_remove)} actor(s)")
    return len(to_remove)


def _cleanup_assets(dest):
    try:
        if _asset_lib().does_directory_exist(dest):
            for a in _asset_lib().list_assets(dest, recursive=True):
                try:
                    _asset_lib().delete_asset(str(a))
                except Exception:
                    pass
    except Exception:
        pass

# ============================================================
# PBR MATERIAL CREATION
# ============================================================

def _create_parent_material(mat_name, dest, textures, mel):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    mat = tools.create_asset(mat_name, dest, unreal.Material, unreal.MaterialFactoryNew())
    if not mat:
        return None
    mp = mat.get_path_name()

    has = lambda ch: ch in textures

    def tex_p(name, x, y, ch):
        tex = _asset_lib().load_asset(textures[ch])
        n = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, x, y)
        n.set_editor_property("parameter_name", name)
        n.set_editor_property("texture", tex)
        return n

    def scalar_p(name, val, x, y):
        n = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, x, y)
        n.set_editor_property("parameter_name", name)
        n.set_editor_property("default_value", val)
        return n

    def mul_n(x, y):
        return mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, x, y)

    def lrp_n(x, y):
        return mel.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, x, y)

    y_pos = 0

    if has("BaseColor"):
        t = tex_p("T_BaseColor", -800, y_pos, "BaseColor")
        mel.connect_material_property(t, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        y_pos += 250

    if has("Normal"):
        t = tex_p("T_Normal", -800, y_pos, "Normal")
        mel.connect_material_property(t, "RGB", unreal.MaterialProperty.MP_NORMAL)
        y_pos += 250

    if has("Roughness"):
        t = tex_p("T_Roughness", -800, y_pos, "Roughness")
        p_min = scalar_p("RoughnessMin", 0.0, -600, y_pos + 50)
        p_max = scalar_p("RoughnessMax", 1.0, -600, y_pos + 100)
        l = lrp_n(-400, y_pos)
        mel.connect_material_expressions(p_min, "", l, "A")
        mel.connect_material_expressions(p_max, "", l, "B")
        mel.connect_material_expressions(t, "R", l, "Alpha")
        mel.connect_material_property(l, "", unreal.MaterialProperty.MP_ROUGHNESS)
        y_pos += 250

    if has("Metallic"):
        t = tex_p("T_Metallic", -800, y_pos, "Metallic")
        mel.connect_material_property(t, "R", unreal.MaterialProperty.MP_METALLIC)
        y_pos += 250

    if has("Emissive"):
        t = tex_p("T_Emissive", -800, y_pos, "Emissive")
        p_int = scalar_p("EmissiveIntensity", 1.0, -600, y_pos + 50)
        m = mul_n(-400, y_pos)
        mel.connect_material_expressions(t, "RGB", m, "A")
        mel.connect_material_expressions(p_int, "", m, "B")
        mel.connect_material_property(m, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        y_pos += 250

    if has("AO"):
        t = tex_p("T_AO", -800, y_pos, "AO")
        mel.connect_material_property(t, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
        y_pos += 250

    if has("Opacity"):
        t = tex_p("T_Opacity", -800, y_pos, "Opacity")
        mel.connect_material_property(t, "R", unreal.MaterialProperty.MP_OPACITY)
        y_pos += 250

    if has("Specular"):
        t = tex_p("T_Specular", -800, y_pos, "Specular")
        mel.connect_material_property(t, "R", unreal.MaterialProperty.MP_SPECULAR)
        y_pos += 250

    if has("Height"):
        tex_p("T_Height", -800, y_pos, "Height")
        y_pos += 250

    try:
        mel.recompile_material(mat)
    except Exception:
        pass
    _asset_lib().save_asset(mp)
    _log(f"  Parent material: {mat_name} ({len(textures)} ch)")
    return mp


def _create_mi(mi_name, dest, parent_path, textures):
    mi_path = f"{dest}/{mi_name}"
    if _asset_lib().does_asset_exist(mi_path):
        mi = _asset_lib().load_asset(mi_path)
        if mi:
            _set_mi_tex(mi, mi_path, textures)
            return mi_path
        _asset_lib().delete_asset(mi_path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    try:
        factory = unreal.MaterialInstanceConstantFactoryNew()
        mi = tools.create_asset(mi_name, dest, unreal.MaterialInstanceConstant, factory)
    except Exception:
        _log("  MI creation failed, using parent", "warning")
        return parent_path

    if not mi:
        return parent_path

    parent = _asset_lib().load_asset(parent_path)
    if parent:
        mi.set_editor_property("parent", parent)

    mi_path = mi.get_path_name()
    _set_mi_tex(mi, mi_path, textures)
    return mi_path


def _set_mi_tex(mi, mi_path, textures):
    mel = unreal.MaterialEditingLibrary
    pmap = {
        "BaseColor": "T_BaseColor", "Normal": "T_Normal", "Roughness": "T_Roughness",
        "Metallic": "T_Metallic", "AO": "T_AO", "Emissive": "T_Emissive",
        "Height": "T_Height", "Opacity": "T_Opacity", "Specular": "T_Specular",
    }
    for ch, tp in textures.items():
        p = pmap.get(ch)
        if not p:
            continue
        tex = _asset_lib().load_asset(tp)
        if not tex:
            continue
        try:
            mel.set_material_instance_texture_parameter_value(mi, p, tex)
        except Exception:
            pass
    _asset_lib().save_asset(mi_path)


def _apply_material_to_actors(actors, mi_path):
    mat = _asset_lib().load_asset(mi_path)
    if not mat:
        return
    count = 0
    for actor in actors:
        try:
            comp = actor.static_mesh_component
            if comp:
                for i in range(comp.get_num_materials()):
                    comp.set_material(i, mat)
                count += 1
        except Exception:
            pass
    if count:
        _log(f"  Applied material to {count} actor(s)")

# ============================================================
# IMPORT TEXTURES + CREATE MATERIALS
# ============================================================

def _process_textures(tex_folder, scene_name, tex_dest=None, mat_dest=None):
    """Import textures and create PBR materials.

    Args:
        tex_folder: Local folder with texture files
        scene_name: Scene name for asset naming
        tex_dest: UEFN destination for textures (default: _tex_dir(scene_name))
        mat_dest: UEFN destination for materials (default: _mat_dir(scene_name))
    """
    if not tex_folder or not os.path.exists(tex_folder):
        return {}

    if tex_dest is None:
        tex_dest = _tex_dir(scene_name)
    if mat_dest is None:
        mat_dest = _mat_dir(scene_name)

    files = [f for f in os.listdir(tex_folder)
             if f.lower().endswith((".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tiff", ".exr"))]
    if not files:
        return {}

    # Group by texture set
    tex_sets = {}
    for f in files:
        ch = _detect_channel(f)
        if not ch:
            continue
        sn = _get_set_name(f)
        if sn not in tex_sets:
            tex_sets[sn] = {}
        tex_sets[sn][ch] = f

    _log(f"  {len(tex_sets)} texture set(s), {len(files)} file(s)")

    mel = unreal.MaterialEditingLibrary
    created_mats = {}

    for sn, channels in tex_sets.items():
        tex_paths = {}
        for ch, fname in channels.items():
            fp = os.path.join(tex_folder, fname)
            if not os.path.isfile(fp):
                continue
            aname = f"T_{scene_name}_{os.path.splitext(fname)[0]}".replace(" ", "_").replace("-", "_")
            ap = _import_tex(fp, tex_dest, aname)
            if ap:
                _config_tex(ap, ch)
                tex_paths[ch] = ap
                _log(f"    {fname} -> {ch}")

        if not tex_paths:
            continue

        parent_name = f"M_{scene_name}_{sn}".replace(" ", "_").replace("-", "_")
        parent_path = _create_parent_material(parent_name, mat_dest, tex_paths, mel)
        if not parent_path:
            continue

        mi_name = f"MI_{scene_name}_{sn}".replace(" ", "_").replace("-", "_")
        mi_path = _create_mi(mi_name, mat_dest, parent_path, tex_paths)
        if mi_path:
            created_mats[sn] = mi_path

    return created_mats

# ============================================================
# COMMAND REGISTRY
# ============================================================

_HANDLERS = {}


def _reg(n):
    def d(fn):
        _HANDLERS[n] = fn
        return fn
    return d


def _dispatch(cmd, par):
    h = _HANDLERS.get(cmd)
    if not h:
        raise ValueError(f"Unknown command: {cmd}")
    return h(**par)


@_reg("ping")
def _ping():
    global _last_import
    return {
        "status": "ok",
        "port": _bound_port,
        "project_path": _project_path,
        "blender_connected": bool(_blender_info),
        "total_imports": _total_imports,
    }


@_reg("get_log")
def _get_log(last_n=50):
    return {"lines": _log_ring[-last_n:]}


@_reg("get_status")
def _get_status():
    return {
        "port": _bound_port,
        "project_path": _project_path,
        "bridge_project": _bridge_project,
        "blender": _blender_info,
        "total_imports": _total_imports,
        "scenes": list(_imported_scenes.keys()),
    }


@_reg("blender_connect")
def _blender_connect(blender_version="", addon_version="", project_name="",
                     server_port=0, **kw):
    global _blender_info, _bridge_project, _blender_server_port
    _bridge_project = project_name.strip() or "Default"
    _blender_server_port = int(server_port) if server_port else 0
    _blender_info = {
        "version": blender_version,
        "addon_version": addon_version,
        "project_name": _bridge_project,
        "connected_at": time.time(),
        "server_port": _blender_server_port,
    }
    _log(f"Blender connected: v{blender_version}, project: {_bridge_project}")
    if _blender_server_port:
        _log(f"  Blender server: :{_blender_server_port}")
    _log(f"  Meshes:    {_mesh_dir()}")
    _log(f"  Materials: {_mat_dir()}")
    _log(f"  Textures:  {_tex_dir()}")
    return {"status": "ok", "project_path": _project_path,
            "bridge_project": _bridge_project}


@_reg("blender_disconnect")
def _blender_disconnect(**kw):
    global _blender_info
    _blender_info = {}
    _log("Blender disconnected")
    return {"status": "ok"}


@_reg("import_scene")
def _import_scene_cmd(fbx_path="", scene_name="Scene", objects=None,
                      selected_only=False, exchange_folder="",
                      combine_meshes=True, **kw):
    global _last_import, _total_imports

    _log(f"IMPORT: {scene_name} ({'selected' if selected_only else 'full'})")

    mesh_dest = _mesh_dir(scene_name)
    mat_dest = _mat_dir(scene_name)
    tex_dest = _tex_dir(scene_name)
    obj_data = objects or []
    incoming_names = [o.get("name", "") for o in obj_data]

    if not fbx_path or not os.path.exists(fbx_path):
        _log(f"FBX not found: {fbx_path}", "error")
        return {"success": False, "error": "FBX not found"}

    # Import textures if available
    tex_folder = os.path.join(exchange_folder, "textures") if exchange_folder else ""
    created_mats = {}
    if tex_folder and os.path.isdir(tex_folder):
        created_mats = _process_textures(tex_folder, scene_name, tex_dest, mat_dest)

    # Import FBX FIRST, before cleaning old actors
    imported = _import_fbx(fbx_path, mesh_dest, combine_meshes)
    mesh_paths = []
    for p in imported:
        cls = _asset_class(p)
        if "StaticMesh" in cls:
            mesh_paths.append(p)
        else:
            # Fallback: try loading directly
            try:
                asset = unreal.load_asset(str(p))
                if asset and isinstance(asset, unreal.StaticMesh):
                    mesh_paths.append(p)
                    _log(f"  Fallback load_asset matched: {p}")
            except Exception:
                pass

    # Nuclear fallback: scan mesh folder if nothing found
    if not mesh_paths:
        _log("  No meshes from paths, scanning folder...")
        try:
            reg = unreal.AssetRegistryHelpers.get_asset_registry()
            for ad in reg.get_assets_by_path(mesh_dest, recursive=True):
                cls = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") else ""
                if "StaticMesh" in cls:
                    mesh_paths.append(str(ad.get_asset().get_path_name()))
        except Exception:
            pass
        if not mesh_paths:
            # Last resort: load every asset in folder
            try:
                for a in _asset_lib().list_assets(mesh_dest, recursive=True):
                    try:
                        asset = unreal.load_asset(str(a))
                        if asset and isinstance(asset, unreal.StaticMesh):
                            mesh_paths.append(str(a))
                    except Exception:
                        pass
            except Exception:
                pass
        _log(f"  Folder scan found: {len(mesh_paths)} mesh(es)")

    _log(f"  Meshes: {len(mesh_paths)} of {len(imported)} imported")

    # Only clean old actors AFTER confirming new meshes are valid
    if not mesh_paths:
        _log("  No valid meshes — keeping existing actors", "warning")
        return {"success": False, "error": "Import produced no valid meshes",
                "actors": 0, "meshes": 0, "materials": len(created_mats)}

    # Safe to clean now — we have valid replacements
    if selected_only:
        _cleanup_actors(set(incoming_names))
    else:
        _cleanup_actors()

    # Place meshes
    actors = _place_meshes(mesh_paths, obj_data, scene_name)

    # Materials come from the FBX import itself (embed_textures=True on the Blender side
    # → UEFN auto-builds a PBR material per mesh, reused by name). We intentionally do NOT
    # override that with a single material here (that was the "first material on all" bug).

    # Save state
    channels = set()
    for state in _imported_scenes.values():
        for cs in state.get("texture_sets", {}).values():
            channels.update(cs.keys())

    _imported_scenes[scene_name] = {
        "actors": len(actors),
        "meshes": len(mesh_paths),
        "materials": created_mats,
        "time": time.time(),
        "selected_only": selected_only,
    }
    _last_import = time.time()
    _total_imports += 1

    _log(f"IMPORT COMPLETE: {len(actors)} actor(s), {len(created_mats)} material(s)")
    return {
        "success": True,
        "actors": len(actors),
        "meshes": len(mesh_paths),
        "materials": len(created_mats),
    }


@_reg("import_baked")
def _import_baked_cmd(fbx_path="", scene_name="Scene", objects=None,
                      exchange_folder="", combine_meshes=True, **kw):
    global _last_import, _total_imports

    _log(f"BAKED IMPORT: {scene_name}")

    mesh_dest = _mesh_dir(scene_name)
    mat_dest = _mat_dir(scene_name)
    tex_dest = _tex_dir(scene_name)
    obj_data = objects or []

    # Full cleanup
    _cleanup_actors()
    _cleanup_assets(mesh_dest)
    _cleanup_assets(mat_dest)
    _cleanup_assets(tex_dest)

    if not fbx_path or not os.path.exists(fbx_path):
        _log(f"FBX not found: {fbx_path}", "error")
        return {"success": False, "error": "FBX not found"}

    # Import baked textures and create PBR materials
    tex_folder = os.path.join(exchange_folder, "textures") if exchange_folder else ""
    created_mats = {}
    if tex_folder and os.path.isdir(tex_folder):
        created_mats = _process_textures(tex_folder, scene_name, tex_dest, mat_dest)

    # Import FBX
    imported = _import_fbx(fbx_path, mesh_dest, combine_meshes)
    mesh_paths = []
    for p in imported:
        cls = _asset_class(p)
        if "StaticMesh" in cls:
            mesh_paths.append(p)
        else:
            try:
                asset = unreal.load_asset(str(p))
                if asset and isinstance(asset, unreal.StaticMesh):
                    mesh_paths.append(p)
                    _log(f"  Fallback matched: {p}")
            except Exception:
                pass
    _log(f"  Meshes found: {len(mesh_paths)} of {len(imported)} imported")

    # Clean FBX-imported materials (we use our PBR ones)
    for p in imported:
        cls = _asset_class(p)
        if "Material" in cls and "Instance" not in cls:
            try:
                _asset_lib().delete_asset(p)
            except Exception:
                pass

    # Place meshes
    actors = _place_meshes(mesh_paths, obj_data, scene_name)

    # Materials come from the FBX import itself (embed_textures=True on the Blender side
    # → UEFN auto-builds a PBR material per mesh, reused by name). We intentionally do NOT
    # override that with a single material here (that was the "first material on all" bug).

    _imported_scenes[scene_name] = {
        "actors": len(actors),
        "meshes": len(mesh_paths),
        "materials": created_mats,
        "time": time.time(),
        "baked": True,
    }
    _last_import = time.time()
    _total_imports += 1

    _log(f"BAKED IMPORT COMPLETE: {len(actors)} actor(s), {len(created_mats)} material(s)")
    return {
        "success": True,
        "actors": len(actors),
        "meshes": len(mesh_paths),
        "materials": len(created_mats),
    }


@_reg("update_transforms")
def _update_transforms_cmd(objects=None, **kw):
    if not objects:
        return {"updated": 0}

    asub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors_map = {}
    for actor in asub.get_all_level_actors():
        label = actor.get_actor_label()
        if label and label.startswith(ACTOR_PREFIX):
            actors_map[label[len(ACTOR_PREFIX):]] = actor

    updated = 0
    for od in objects:
        name = od.get("name", "")
        actor = actors_map.get(name)
        if not actor:
            continue

        loc = od.get("location", [0, 0, 0])
        rot = od.get("rotation", [0, 0, 0])
        scale = od.get("scale", [1, 1, 1])

        actor.set_actor_location(
            unreal.Vector(loc[0], loc[1], loc[2]), False, False)
        actor.set_actor_rotation(
            unreal.Rotator(pitch=rot[0], yaw=rot[1], roll=rot[2]), False)
        actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))

        # Update folder from collection path
        collection = od.get("collection", "")
        if collection:
            actor.set_folder_path(f"/BlenderBridge/{collection}")
        else:
            actor.set_folder_path("/BlenderBridge")

        updated += 1

    return {"updated": updated}


@_reg("add_objects")
def _add_objects_cmd(fbx_paths=None, objects=None, scene_name="Scene",
                     exchange_folder="", **kw):
    """Import and place new objects without touching existing actors."""
    if not fbx_paths:
        return {"added": 0}

    mesh_dest = _mesh_dir(scene_name)
    obj_data = objects or []

    # Import each rep mesh FBX (one per unique datablock), collect all StaticMeshes, then place
    # ALL objects together so instances can reference a shared mesh (object-driven — F-44).
    all_mesh_paths = []
    for obj_name, fbx_path in fbx_paths.items():
        if not os.path.exists(fbx_path):
            _log(f"  FBX not found for {obj_name}: {fbx_path}", "warning")
            continue
        imported = _import_fbx(fbx_path, mesh_dest, combine=False)
        all_mesh_paths += [p for p in imported if "StaticMesh" in _asset_class(p)]

    if not all_mesh_paths:
        _log("  No meshes imported", "warning")
        return {"added": 0}

    actors = _place_meshes(all_mesh_paths, obj_data, scene_name)
    _log(f"Added {len(actors)} object(s)")
    return {"added": len(actors)}


@_reg("remove_objects")
def _remove_objects_cmd(names=None, **kw):
    """Remove specific actors by name."""
    if not names:
        return {"removed": 0}

    count = _cleanup_actors(set(names))
    _log(f"Removed {count} object(s): {names}")
    return {"removed": count}


@_reg("update_objects")
def _update_objects_cmd(fbx_paths=None, objects=None, scene_name="Scene",
                        exchange_folder="", **kw):
    """Update specific objects — remove old actors, import new FBX, place new actors."""
    if not fbx_paths:
        return {"updated": 0}

    mesh_dest = _mesh_dir(scene_name)
    obj_data = objects or []

    # Remove old actors for ALL objects being updated (not just the rep FBX names) — actors are
    # labeled by object name, and instances share a rep FBX.
    names_to_update = [od.get("name", "") for od in obj_data] or list(fbx_paths.keys())
    _cleanup_actors(set(n for n in names_to_update if n))

    # Re-import each rep mesh, then re-place ALL objects together (object-driven — F-44).
    all_mesh_paths = []
    for obj_name, fbx_path in fbx_paths.items():
        if not os.path.exists(fbx_path):
            _log(f"  FBX not found for {obj_name}: {fbx_path}", "warning")
            continue
        imported = _import_fbx(fbx_path, mesh_dest, combine=False)
        all_mesh_paths += [p for p in imported if "StaticMesh" in _asset_class(p)]

    if not all_mesh_paths:
        _log("  No meshes imported", "warning")
        return {"updated": 0}

    actors = _place_meshes(all_mesh_paths, obj_data, scene_name)
    _log(f"Updated {len(actors)} object(s)")
    return {"updated": len(actors)}


@_reg("update_materials")
def _update_materials_cmd(materials=None, **kw):
    """Update material properties on existing actors without FBX re-import."""
    if not materials:
        return {"updated": 0}

    asub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors_map = {}
    for actor in asub.get_all_level_actors():
        label = actor.get_actor_label()
        if label and label.startswith(ACTOR_PREFIX):
            actors_map[label[len(ACTOR_PREFIX):]] = actor

    updated = 0
    for mat_info in materials:
        obj_name = mat_info.get("object_name", "")
        actor = actors_map.get(obj_name)
        if not actor:
            continue

        try:
            comp = actor.get_component_by_class(unreal.StaticMeshComponent)
            if not comp:
                continue

            # Get current material or create overlay
            num_mats = comp.get_num_materials()
            for i in range(max(1, num_mats)):
                existing = comp.get_material(i)
                dmi = comp.create_dynamic_material_instance(
                    i, existing, f"DMI_{obj_name}")
                if not dmi:
                    continue

                # Set base color
                bc = mat_info.get("base_color", mat_info.get("diffuse_color"))
                if bc and len(bc) >= 3:
                    dmi.set_vector_parameter_value(
                        "Base Color",
                        unreal.LinearColor(bc[0], bc[1], bc[2], bc[3] if len(bc) > 3 else 1.0))

                # Set scalar parameters
                for param, key in [("Metallic", "metallic"),
                                   ("Roughness", "roughness"),
                                   ("Specular", "specular")]:
                    val = mat_info.get(key)
                    if val is not None:
                        dmi.set_scalar_parameter_value(param, float(val))

            updated += 1
        except Exception as e:
            _log(f"  Material update failed for {obj_name}: {e}", "warning")

    _log(f"Updated materials on {updated} actor(s)")
    return {"updated": updated}


@_reg("update_textures")
def _update_textures_cmd(object_names=None, exchange_folder="",
                         scene_name="Scene", **kw):
    """Re-import textures and rebind material instances without FBX re-import."""
    if not object_names:
        return {"updated": 0}

    tex_dest = _tex_dir(scene_name)
    mat_dest = _mat_dir(scene_name)

    # Read material info JSON from exchange folder
    mat_json = os.path.join(exchange_folder, "materials.json") if exchange_folder else ""
    mat_entries = []
    if mat_json and os.path.isfile(mat_json):
        try:
            with open(mat_json, "r") as f:
                mat_entries = json.load(f)
        except Exception as e:
            _log(f"  Failed to read materials.json: {e}", "warning")

    tex_folder = os.path.join(exchange_folder, "textures") if exchange_folder else ""
    if not tex_folder or not os.path.isdir(tex_folder):
        _log("  No texture folder found", "warning")
        return {"updated": 0}

    # Import texture files and build channel mapping
    imported_textures = {}  # {image_name: uefn_asset_path}
    for fname in os.listdir(tex_folder):
        if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tiff", ".exr")):
            continue
        fp = os.path.join(tex_folder, fname)
        aname = f"T_{scene_name}_{os.path.splitext(fname)[0]}".replace(" ", "_").replace("-", "_")
        ap = _import_tex(fp, tex_dest, aname)
        if ap:
            ch = _detect_channel(fname)
            if ch:
                _config_tex(ap, ch)
            imported_textures[os.path.splitext(fname)[0]] = ap
            # Also map by full filename stem
            img_name = os.path.splitext(fname)[0]
            imported_textures[img_name] = ap
            _log(f"  Texture: {fname} -> {ap}")

    if not imported_textures:
        _log("  No textures imported", "warning")
        return {"updated": 0}

    # Try to find and update existing Material Instances
    scene_state = _imported_scenes.get(scene_name, {})
    existing_mats = scene_state.get("materials", {})
    updated = 0

    if existing_mats:
        # Rebind textures on existing MIs
        for sn, mi_path in existing_mats.items():
            mi = _asset_lib().load_asset(mi_path)
            if not mi:
                continue
            # Build tex_paths for this MI from imported textures
            tex_paths = {}
            for img_name, ap in imported_textures.items():
                ch = _detect_channel(img_name)
                if ch:
                    tex_paths[ch] = ap
            if tex_paths:
                _set_mi_tex(mi, mi_path, tex_paths)
                updated += 1
                _log(f"  Rebound MI: {mi_path}")
    else:
        # No existing MIs — create via full pipeline
        created_mats = _process_textures(tex_folder, scene_name, tex_dest, mat_dest)
        if created_mats:
            # Apply to actors
            asub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            obj_set = set(object_names)
            for actor in asub.get_all_level_actors():
                label = actor.get_actor_label()
                if not label or not label.startswith(ACTOR_PREFIX):
                    continue
                obj_name = label[len(ACTOR_PREFIX):]
                if obj_name in obj_set:
                    first_mat = list(created_mats.values())[0]
                    _apply_material_to_actors([actor], first_mat)
                    updated += 1

            # Store for future rebinds
            if scene_name in _imported_scenes:
                _imported_scenes[scene_name]["materials"] = created_mats
            else:
                _imported_scenes[scene_name] = {"materials": created_mats}

    _log(f"Updated textures on {updated} material(s)")
    return {"updated": updated}


# ============================================================
# BIDIRECTIONAL SYNC — UEFN → Blender
# ============================================================

def _read_bb_transforms():
    """Read transforms of all BB_ actors in the level."""
    asub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    result = []
    for actor in asub.get_all_level_actors():
        label = actor.get_actor_label()
        if not label or not label.startswith(ACTOR_PREFIX):
            continue
        obj_name = label[len(ACTOR_PREFIX):]
        loc = actor.get_actor_location()
        rot = actor.get_actor_rotation()
        sc = actor.get_actor_scale3d()
        result.append({
            "name": obj_name,
            "location": [loc.x, loc.y, loc.z],
            "rotation": [rot.pitch, rot.yaw, rot.roll],
            "scale": [sc.x, sc.y, sc.z],
        })
    return result


def _dirty_map_count():
    """Number of dirty map packages. Under OFPA/World Partition a moved actor dirties its own
    external (__ExternalActors__) package, which counts as a map package; saving clears it — so
    a DROP in this count means a save happened. This is the only dirty API this UEFN build
    exposes to Python (actor.get_package/get_outermost raise AttributeError here)."""
    try:
        return len(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())
    except Exception:
        return -1


def _transform_hash(t):
    """Hash a single transform dict for diff comparison."""
    loc = t.get("location", [0, 0, 0])
    rot = t.get("rotation", [0, 0, 0])
    sc = t.get("scale", [1, 1, 1])
    # Round to avoid float noise (0.01 precision is ~1mm in UE units)
    key = "|".join(f"{v:.2f}" for v in loc + rot + sc)
    return key


def _snapshot_transforms(transforms):
    """Build a {name: hash} snapshot from a transform list."""
    return {t["name"]: _transform_hash(t) for t in transforms}


def _diff_transforms(transforms):
    """Return only transforms that changed since last push. Updates snapshot."""
    global _last_push_snapshot
    new_snap = _snapshot_transforms(transforms)
    changed = []
    for t in transforms:
        name = t["name"]
        if new_snap[name] != _last_push_snapshot.get(name):
            changed.append(t)
    _last_push_snapshot = new_snap
    return changed


def _push_to_blender(port=0, transforms=None):
    """HTTP POST transforms to Blender's server."""
    if not port:
        port = _blender_server_port
    if not port:
        _log("No Blender server port — cannot push", "warning")
        return False
    if not transforms:
        _log("No transforms to push", "warning")
        return False

    payload = json.dumps({
        "command": "push_transforms",
        "params": {"objects": transforms},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("success"):
                _log(f"Pushed {len(transforms)} transform(s) to Blender")
                return True
            else:
                _log(f"Blender rejected push: {body.get('error', '?')}", "warning")
                return False
    except Exception as e:
        _log(f"Push to Blender failed: {e}", "error")
        return False


def _refresh_push_snapshot():
    """Reset the push baseline to the current actor transforms. Called after applying
    inbound changes from Blender so the live poll does NOT bounce them straight back."""
    global _last_push_snapshot
    if not _live_sync_active:
        return
    try:
        _last_push_snapshot = _snapshot_transforms(_read_bb_transforms())
    except Exception:
        pass


@_reg("set_live_sync")
def _set_live_sync_cmd(active=False, blender_port=0, **kw):
    """Blender toggles Live Sync — when on, UEFN pushes actor edits back to Blender on each
    UEFN save (Ctrl+S). Carries Blender's server port so a toggle re-establishes it even after
    the UEFN script was re-run (which resets these globals). Baseline reset on enable."""
    global _live_sync_active, _last_push_snapshot, _blender_server_port
    _live_sync_active = bool(active)
    if blender_port:
        _blender_server_port = int(blender_port)
    if _live_sync_active:
        try:
            _last_push_snapshot = _snapshot_transforms(_read_bb_transforms())
        except Exception:
            _last_push_snapshot = {}
        _log(f"Live Sync ON — push on Ctrl+S (port {_blender_server_port}, {_dirty_map_count()} dirty)")
    else:
        _log("Live Sync OFF")
    return {"live": _live_sync_active, "blender_port": _blender_server_port}


@_reg("request_push_transforms")
def _request_push_transforms_cmd(blender_port=0, **kw):
    """Blender requests UEFN to push current actor transforms back (full sync)."""
    global _last_push_snapshot
    port = int(blender_port) if blender_port else _blender_server_port
    if not port:
        return {"error": "No Blender server port"}

    transforms = _read_bb_transforms()
    if not transforms:
        return {"pushed": 0, "message": "No BB_ actors found"}

    ok = _push_to_blender(port, transforms)
    if ok:
        _last_push_snapshot = _snapshot_transforms(transforms)
    return {"pushed": len(transforms) if ok else 0}


@_reg("push_transforms_to_blender")
def _push_transforms_to_blender_cmd(**kw):
    """Dashboard button: push all BB_ transforms to Blender (full sync)."""
    global _last_push_snapshot
    if not _blender_server_port:
        _log("Blender server port unknown — connect from Blender first", "warning")
        return {"error": "No Blender server port"}

    transforms = _read_bb_transforms()
    if not transforms:
        _log("No BB_ actors to push", "warning")
        return {"pushed": 0}

    ok = _push_to_blender(_blender_server_port, transforms)
    if ok:
        _last_push_snapshot = _snapshot_transforms(transforms)
    return {"pushed": len(transforms) if ok else 0}


@_reg("clean_all")
def _clean_all_cmd(**kw):
    if not kw.get("confirm"):
        return {"error": "clean_all requires confirm=True (destructive: deletes BB_ actors + imported assets)"}
    count = _cleanup_actors()
    # Clean assets from THIS session's imports (Meshes, Materials, Textures)
    cleaned_folders = []
    for scene_name in list(_imported_scenes.keys()):
        for dir_fn in (_mesh_dir, _mat_dir, _tex_dir):
            dest = dir_fn(scene_name)
            _cleanup_assets(dest)
            try:
                if _asset_lib().does_directory_exist(dest):
                    _asset_lib().delete_directory(dest)
            except Exception:
                pass
        cleaned_folders.append(scene_name)
    _imported_scenes.clear()
    _log(f"Cleaned: {count} actor(s), {len(cleaned_folders)} scene(s)")
    return {"cleaned": count, "folders": len(cleaned_folders)}


@_reg("execute_python")
def _exec_py(code=""):
    # Disabled by default — power-user/debug hook. Opt in with env BB_ENABLE_EXEC=1.
    if os.environ.get("BB_ENABLE_EXEC") != "1":
        return {"error": "execute_python is disabled (set BB_ENABLE_EXEC=1 to enable)"}
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    g = {"__builtins__": __builtins__, "unreal": unreal, "result": None}
    try:
        sys.stdout, sys.stderr = out, err
        exec(code, g)
    except Exception:
        traceback.print_exc(file=err)
    finally:
        sys.stdout, sys.stderr = old
    return {"result": _serialize(g.get("result")), "stdout": out.getvalue(), "stderr": err.getvalue()}

# ============================================================
# HTTP SERVER
# ============================================================

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({"status": "ok", "port": _bound_port}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        global _req_counter
        L = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(L)
        try:
            body = json.loads(raw)
        except Exception:
            self._e(400, "Bad JSON")
            return
        cmd = body.get("command", "")
        par = body.get("params", {})
        if not cmd:
            self._e(400, "No command")
            return
        if cmd == "ping":
            b = json.dumps({"success": True, "result": {
                "status": "ok", "port": _bound_port,
                "project_path": _project_path,
                "blender_connected": bool(_blender_info),
            }}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b)
            return

        _req_counter += 1
        rid = f"r_{_req_counter}_{time.time_ns()}"
        _command_queue.put((rid, cmd, par))
        dl = time.time() + HTTP_TIMEOUT
        while time.time() < dl:
            with _responses_lock:
                if rid in _responses:
                    res = _responses.pop(rid)
                    break
            time.sleep(POLL_INTERVAL)
        else:
            self._e(504, "Timeout")
            return

        b = json.dumps(res).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        # No CORS — loopback-only; the legitimate client is Blender (urllib), not a browser.
        self.send_response(204)
        self.end_headers()

    def _e(self, c, m):
        b = json.dumps({"success": False, "error": m}).encode()
        self.send_response(c)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def _start_http():
    global _http_server, _http_thread, _bound_port
    if _http_server:
        return _bound_port
    for p in range(DEFAULT_PORT, MAX_PORT + 1):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            _http_server = HTTPServer(("127.0.0.1", p), _H)
            _bound_port = p
            _http_thread = threading.Thread(target=_http_server.serve_forever, daemon=True)
            _http_thread.start()
            _log(f"HTTP server on :{p}")
            return p
        except OSError:
            continue
    raise RuntimeError("No free port in range")


def _stop_http():
    global _http_server, _http_thread, _bound_port
    if not _http_server:
        return
    _http_server.shutdown()
    if _http_thread:
        _http_thread.join(timeout=3)
    _http_server = None
    _http_thread = None
    _bound_port = 0

# ============================================================
# TKINTER DASHBOARD
# ============================================================

import tkinter as tk


class Dashboard:
    def __init__(self, port):
        self.port = port
        self.root = tk.Tk()
        self.root.title(f"Blender Bridge :{port}")
        self.root.geometry("480x780")
        self.root.configure(bg="#0d1117")
        self.root.attributes("-topmost", True)

        # Theme
        self.BG = "#0d1117"
        self.BG2 = "#161b22"
        self.BG3 = "#21262d"
        self.FG = "#e6edf3"
        self.DIM = "#8b949e"
        self.ACC = "#58a6ff"
        self.GRN = "#3fb950"
        self.RED = "#f85149"
        self.ORG = "#d29922"
        self.PRP = "#bc8cff"
        self.BRD = "#30363d"

        self.tick_h = None
        self.fc = 0
        self._sh = ""
        self._poll_next = 0.0        # debounce for the UEFN->Blender save check
        self._dirty_n = 0            # prior dirty map-package count (save = count drops)

        self._build()
        self._on_path()
        self._on_set()
        self._start_tick()

    def _build(self):
        bg = self.BG

        # Header
        h = tk.Frame(self.root, bg=bg)
        h.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(h, text="BLENDER", font=("Segoe UI", 14, "bold"),
                 fg=self.ACC, bg=bg).pack(side="left")
        tk.Label(h, text="  \u2194  ", font=("Segoe UI", 14),
                 fg=self.BRD, bg=bg).pack(side="left")
        tk.Label(h, text="UEFN", font=("Segoe UI", 14, "bold"),
                 fg=self.PRP, bg=bg).pack(side="left")
        self.dot = tk.Label(h, text="\u25cf", font=("Segoe UI", 16),
                            fg=self.DIM, bg=bg)
        self.dot.pack(side="right")
        self.clbl = tk.Label(h, text="Waiting...", font=("Segoe UI", 8),
                             fg=self.DIM, bg=bg)
        self.clbl.pack(side="right", padx=(0, 6))

        tk.Frame(self.root, bg=self.BRD, height=1).pack(fill="x", padx=16, pady=6)

        # Project info
        pp = tk.Frame(self.root, bg=bg)
        pp.pack(fill="x", padx=16)
        tk.Label(pp, text="UEFN PATH", font=("Segoe UI", 7, "bold"),
                 fg=self.DIM, bg=bg).pack(side="left")
        self.pv = tk.StringVar(value=_project_path or "/Game")
        pe = tk.Entry(pp, textvariable=self.pv, bg="#0d1117", fg=self.FG,
                      insertbackground=self.FG, font=("Consolas", 10),
                      relief="flat", highlightbackground=self.BRD,
                      highlightthickness=1)
        pe.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=4)
        pe.bind("<KeyRelease>", self._on_path)

        # Bridge project name display
        bp = tk.Frame(self.root, bg=bg)
        bp.pack(fill="x", padx=16, pady=(4, 0))
        tk.Label(bp, text="BRIDGE PROJECT", font=("Segoe UI", 7, "bold"),
                 fg=self.DIM, bg=bg).pack(side="left")
        self.bp_v = tk.StringVar(value=_bridge_project or "Waiting...")
        tk.Label(bp, textvariable=self.bp_v, font=("Consolas", 10),
                 fg=self.ACC, bg=bg).pack(side="left", padx=(8, 0))

        # Settings
        sf = tk.Frame(self.root, bg=bg)
        sf.pack(fill="x", padx=16, pady=(6, 0))
        tk.Label(sf, text="Scale:", fg=self.DIM, bg=bg,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 4))
        self.sv = tk.StringVar(value="1.0")
        se = tk.Entry(sf, textvariable=self.sv, bg="#0d1117", fg=self.FG,
                      insertbackground=self.FG, font=("Consolas", 9),
                      relief="flat", width=5, highlightbackground=self.BRD,
                      highlightthickness=1)
        se.pack(side="left", ipady=2)

        tk.Frame(self.root, bg=self.BRD, height=1).pack(fill="x", padx=16, pady=6)

        # Status cards
        cs = tk.Frame(self.root, bg=bg)
        cs.pack(fill="x", padx=16)
        r1 = tk.Frame(cs, bg=bg)
        r1.pack(fill="x", pady=2)
        self._scard(r1, "LISTENER", f":{self.port}", self.GRN, "left")
        self.bv = tk.StringVar(value="Waiting...")
        self._vcard(r1, "BLENDER", self.bv, "right")
        r2 = tk.Frame(cs, bg=bg)
        r2.pack(fill="x", pady=2)
        self.li_v = tk.StringVar(value="\u2014")
        self._vcard(r2, "LAST IMPORT", self.li_v, "left")
        self.ti_v = tk.StringVar(value="0")
        self._vcard(r2, "TOTAL IMPORTS", self.ti_v, "right")

        tk.Frame(self.root, bg=self.BRD, height=1).pack(fill="x", padx=16, pady=6)

        # Imported scenes
        mh = tk.Frame(self.root, bg=bg)
        mh.pack(fill="x", padx=16)
        tk.Label(mh, text="IMPORTED SCENES", font=("Segoe UI", 8, "bold"),
                 fg=self.DIM, bg=bg).pack(side="left")
        self.sc = tk.Label(mh, text="0", font=("Segoe UI", 8, "bold"),
                           fg=self.ACC, bg=bg)
        self.sc.pack(side="right")
        self.sf = tk.Frame(self.root, bg=bg)
        self.sf.pack(fill="x", padx=16, pady=4)
        self._wait_msg()

        tk.Frame(self.root, bg=self.BRD, height=1).pack(fill="x", padx=16, pady=6)

        # Actions
        tk.Label(self.root, text="ACTIONS", font=("Segoe UI", 8, "bold"),
                 fg=self.DIM, bg=bg).pack(padx=16, anchor="w")
        ab = tk.Frame(self.root, bg=bg)
        ab.pack(fill="x", padx=16, pady=6)
        self._btn(ab, "Clean All", self._clean, self.ACC)
        self._btn(ab, "Apply to Selected", self._apply, "#1f6feb")
        self._btn(ab, "Reset", self._reset, self.BG3, fg=self.RED)

        # Sync actions
        sb = tk.Frame(self.root, bg=bg)
        sb.pack(fill="x", padx=16, pady=(0, 6))
        self._btn(sb, "Push to Blender", self._push_bl, self.PRP)

        tk.Frame(self.root, bg=self.BRD, height=1).pack(fill="x", padx=16, pady=6)

        # Log
        lh = tk.Frame(self.root, bg=bg)
        lh.pack(fill="x", padx=16)
        tk.Label(lh, text="ACTIVITY", font=("Segoe UI", 8, "bold"),
                 fg=self.DIM, bg=bg).pack(side="left")
        tk.Button(lh, text="Clear", font=("Segoe UI", 7), fg=self.DIM,
                  bg=bg, relief="flat", bd=0, cursor="hand2",
                  command=self._cl).pack(side="right")
        self.log = tk.Text(self.root, height=6, bg=self.BG2, fg=self.DIM,
                           font=("Consolas", 8), relief="flat", state="disabled",
                           highlightthickness=0, padx=8, pady=6)
        self.log.pack(fill="both", expand=True, padx=16, pady=(4, 4))
        self.log.tag_configure("error", foreground=self.RED)
        self.log.tag_configure("sync", foreground=self.GRN)

        # Footer
        ft = tk.Frame(self.root, bg=self.BG2, height=24)
        ft.pack(fill="x", side="bottom")
        ft.pack_propagate(False)
        tk.Label(ft, text="by KiKoZl \u2022 Surprise Co.",
                 font=("Segoe UI", 7), fg="#484f58", bg=self.BG2).pack(side="left", padx=8)
        tk.Label(ft, text="github.com/KiKoZl1",
                 font=("Segoe UI", 7), fg="#484f58", bg=self.BG2).pack(side="right", padx=8)

    # --- Widgets ---

    def _scard(self, p, l, v, c, s):
        f = tk.Frame(p, bg=self.BG2, highlightbackground=self.BRD, highlightthickness=1)
        f.pack(side=s, expand=True, fill="x", padx=2, ipady=6)
        tk.Label(f, text=l, font=("Segoe UI", 7, "bold"), fg=self.DIM, bg=self.BG2).pack()
        tk.Label(f, text=v, font=("Segoe UI", 11, "bold"), fg=c, bg=self.BG2).pack()

    def _vcard(self, p, l, v, s):
        f = tk.Frame(p, bg=self.BG2, highlightbackground=self.BRD, highlightthickness=1)
        f.pack(side=s, expand=True, fill="x", padx=2, ipady=6)
        tk.Label(f, text=l, font=("Segoe UI", 7, "bold"), fg=self.DIM, bg=self.BG2).pack()
        tk.Label(f, textvariable=v, font=("Segoe UI", 11, "bold"), fg=self.FG, bg=self.BG2).pack()

    def _btn(self, p, t, c, bg, fg=None):
        b = tk.Button(p, text=t, font=("Segoe UI", 8, "bold"), bg=bg,
                      fg=fg or "#fff", relief="flat", cursor="hand2",
                      command=c, padx=8, pady=4)
        b.pack(side="left", expand=True, fill="x", padx=2)
        o = bg
        b.bind("<Enter>", lambda e: b.configure(bg=self._li(o)))
        b.bind("<Leave>", lambda e: b.configure(bg=o))

    def _li(self, h):
        try:
            r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
            return f"#{min(255,r+25):02x}{min(255,g+25):02x}{min(255,b+25):02x}"
        except Exception:
            return h

    def _wait_msg(self):
        for w in self.sf.winfo_children():
            w.destroy()
        tk.Label(self.sf, text="Waiting for Blender...\nInstall the addon and click Connect",
                 fg=self.DIM, bg=self.BG, font=("Segoe UI", 8),
                 justify="left").pack(pady=8)

    # --- Callbacks ---

    def _on_path(self, e=None):
        global _project_path
        _project_path = self.pv.get().strip()
        if _project_path and not _project_path.startswith("/"):
            _project_path = "/" + _project_path

    def _on_set(self):
        global _import_scale
        try:
            _import_scale = float(self.sv.get())
        except Exception:
            _import_scale = 1.0

    def _on_log(self, e, l="info"):
        try:
            self.log.configure(state="normal")
            tag = "error" if l == "error" else ("sync" if "COMPLETE" in e else "")
            self.log.insert("end", e + "\n", tag)
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            pass

    def _cl(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _clean(self):
        _command_queue.put((f"g_{time.time_ns()}", "clean_all", {}))
        _log("Clean All requested")

    def _apply(self):
        if not _imported_scenes:
            _log("No imported scenes", "warning")
            return
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()
        if not actors:
            _log("No selection", "warning")
            return
        scene = list(_imported_scenes.values())[-1]
        mats = scene.get("materials", {})
        if not mats:
            _log("No materials to apply", "warning")
            return
        fm = _asset_lib().load_asset(list(mats.values())[0])
        if not fm:
            return
        c = 0
        for a in actors:
            try:
                comp = a.static_mesh_component
                if comp:
                    for i in range(comp.get_num_materials()):
                        comp.set_material(i, fm)
                    c += 1
            except Exception:
                pass
        _log(f"Applied to {c} actor(s)")

    def _reset(self):
        global _imported_scenes, _blender_info, _total_imports
        _cleanup_actors()
        _imported_scenes = {}
        _blender_info = {}
        _total_imports = 0
        _log("Reset complete")

    def _push_bl(self):
        _command_queue.put((f"g_{time.time_ns()}", "push_transforms_to_blender", {}))
        _log("Push to Blender requested")

    # --- Update ---

    def _upd(self):
        now = time.time()

        # Blender status
        if _blender_info:
            ca = _blender_info.get("connected_at", 0)
            if now - ca < 3600:
                self.dot.configure(fg=self.GRN)
                self.clbl.configure(text="Connected", fg=self.GRN)
                v = _blender_info.get("version", "?")
                self.bv.set(f"v{v}")
            else:
                self.dot.configure(fg=self.ORG)
                self.clbl.configure(text="Idle", fg=self.ORG)
                self.bv.set("Idle")
            self.bp_v.set(_bridge_project or "Default")
        else:
            self.dot.configure(fg=self.DIM)
            self.clbl.configure(text="Waiting...", fg=self.DIM)
            self.bv.set("Waiting...")
            self.bp_v.set("Waiting...")

        # Import stats
        if _last_import > 0:
            e = int(now - _last_import)
            self.li_v.set(f"{e}s ago" if e < 60 else f"{e // 60}m ago")
        self.ti_v.set(str(_total_imports))
        self.sc.configure(text=str(len(_imported_scenes)))

        # Rebuild scene list if changed
        h = str([(n, s.get("actors", 0)) for n, s in _imported_scenes.items()])
        if h != self._sh:
            self._sh = h
            self._reb()

    def _reb(self):
        for w in self.sf.winfo_children():
            w.destroy()
        if not _imported_scenes:
            self._wait_msg()
            return
        for name, state in _imported_scenes.items():
            card = tk.Frame(self.sf, bg=self.BG2,
                            highlightbackground=self.BRD, highlightthickness=1)
            card.pack(fill="x", pady=2)
            r1 = tk.Frame(card, bg=self.BG2)
            r1.pack(fill="x", padx=8, pady=(6, 2))
            tk.Label(r1, text="\u25cf", fg=self.GRN, bg=self.BG2,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(r1, text=f" {name}", fg=self.FG, bg=self.BG2,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            ac = state.get("actors", 0)
            tk.Label(r1, text=f"{ac} actor{'s' if ac != 1 else ''}",
                     fg=self.DIM, bg=self.BG2,
                     font=("Segoe UI", 7)).pack(side="right")

            # Channel badges
            r2 = tk.Frame(card, bg=self.BG2)
            r2.pack(fill="x", padx=8, pady=(0, 2))
            mats = state.get("materials", {})
            if mats:
                tk.Label(r2, text=f"{len(mats)} material(s)",
                         fg=self.DIM, bg=self.BG2,
                         font=("Segoe UI", 7)).pack(side="left")
            if state.get("baked"):
                tk.Label(r2, text=" BAKED ", fg="#fff", bg=self.ORG,
                         font=("Segoe UI", 6, "bold"), padx=3).pack(side="left", padx=1)

            r3 = tk.Frame(card, bg=self.BG2)
            r3.pack(fill="x", padx=8, pady=(0, 6))
            nm = state.get("meshes", 0)
            tk.Label(r3, text=f"{nm} mesh(es)",
                     fg=self.DIM, bg=self.BG2,
                     font=("Segoe UI", 7)).pack(side="left")

    # --- Tick ---

    def _start_tick(self):
        def tick(dt):
            try:
                if not self.root.winfo_exists():
                    self._stop()
                    return

                n = 0
                wrote_transforms = False
                while not _command_queue.empty() and n < TICK_BATCH:
                    try:
                        rid, cmd, par = _command_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        res = _dispatch(cmd, par)
                        resp = {"success": True, "result": _serialize(res)}
                    except Exception as e:
                        _log(f"'{cmd}' failed: {e}\n{traceback.format_exc()}", "error")
                        resp = {"success": False, "error": str(e)}
                    if cmd in INBOUND_WRITE_CMDS:
                        wrote_transforms = True
                    with _responses_lock:
                        _responses[rid] = resp
                    n += 1

                # A Blender-originated write moved/placed actors — absorb it into the push
                # baseline so the live poll below doesn't echo it straight back to Blender.
                if wrote_transforms and _live_sync_active:
                    _refresh_push_snapshot()

                # Clean stale responses
                now = time.time()
                with _responses_lock:
                    for k in [k for k in _responses
                              if float(k.split("_")[-1]) / 1e9 < now - STALE_SEC]:
                        del _responses[k]

                # Live Sync (UEFN -> Blender): push on SAVE (Ctrl+S), mirroring Blender->UEFN —
                # not on every drag (that lagged). A save is the dirty->clean transition of the
                # BB_ actors' own packages (World-Partition safe). Debounced via _poll_next.
                if (_live_sync_active and _blender_server_port
                        and now > self._poll_next):
                    self._poll_next = now + LIVE_POLL_INTERVAL
                    try:
                        n_dirty = _dirty_map_count()
                        if n_dirty >= 0 and n_dirty != self._dirty_n:
                            if n_dirty > self._dirty_n:
                                _log(f"UEFN edits detected ({n_dirty} unsaved) — push on Ctrl+S")
                            else:
                                # count dropped = packages were saved
                                all_transforms = _read_bb_transforms()
                                changed = _diff_transforms(all_transforms) if all_transforms else []
                                if changed:
                                    _log(f"Saved — pushing {len(changed)} change(s) to Blender")
                                    _push_to_blender(_blender_server_port, changed)
                                else:
                                    _log("Saved — no Blender-relevant changes")
                            self._dirty_n = n_dirty
                    except Exception:
                        pass

                self.fc += 1
                if self.fc % 30 == 0:
                    self._upd()
                self.root.update()
            except tk.TclError:
                self._stop()

        self.tick_h = unreal.register_slate_post_tick_callback(tick)
        _log("Dashboard ready")

    def _stop(self):
        if self.tick_h:
            unreal.unregister_slate_post_tick_callback(self.tick_h)
            self.tick_h = None
        _stop_http()
        _log("Dashboard closed")

# ============================================================
# STARTUP
# ============================================================

_detect_project_path()
port = _start_http()
_gui = Dashboard(port)
