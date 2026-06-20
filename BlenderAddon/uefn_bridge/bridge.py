"""UEFN Blender Bridge — Blender Side.

Blender addon that exports FBX + textures to UEFN via HTTP bridge.
Supports: full scene, selected objects, baked textures, live sync.

Developed by KiKoZl (Surprise Co.)

github.com/KiKoZl1 | Surprise Co. (surpriseugc.com)
"""

import bpy
import hashlib
import json
import math
import mathutils
import os
import queue as _queue_mod
import struct
import tempfile
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError
from bpy.app.handlers import persistent

from . import coords  # pure Blender<->UEFN convention (single source, unit-tested)

# ============================================================
# CONFIG
# ============================================================

UEFN_HOST = "127.0.0.1"
UEFN_PORT = 8790
BLENDER_SERVER_PORT = 8791
BLENDER_MAX_PORT = 8795
EXCHANGE_DIR = os.path.join(tempfile.gettempdir(), "UEFNBlenderBridge")
ADDON_VERSION = "0.5.0-beta"

# ============================================================
# STATE
# ============================================================


class BridgeState:
    connected = False
    host = UEFN_HOST
    port = UEFN_PORT
    status = "Disconnected"
    last_obj_hashes = {}       # {name: {"geo","mat","tex","trans"}}
    last_object_names = set()  # set of obj names from last sync
    last_send_time = 0.0
    live_active = False
    send_count = 0
    project_path = ""
    project_name = ""          # bridge project name (folder in UEFN)
    # Bidirectional sync
    receiving = False          # True when applying inbound changes (suppresses outbound)
    server_port = 0            # Blender HTTP server port
    _http_server = None
    _http_thread = None


_st = BridgeState()

# ============================================================
# LOG
# ============================================================


def _log(m):
    print(f"[UEFNBridge] {m}")


def _err(m):
    print(f"[UEFNBridge ERROR] {m}")

# ============================================================
# HTTP CLIENT
# ============================================================


def _url():
    return f"http://{_st.host}:{_st.port}"


def _send(cmd, params=None, timeout=120.0):
    """Send command to UEFN bridge server."""
    payload = json.dumps({"command": cmd, "params": params or {}}).encode()
    req = Request(_url(), data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except URLError as e:
        if "Connection refused" in str(e) or "No connection" in str(e):
            raise ConnectionError("UEFN not reachable") from e
        raise
    if not body.get("success", False):
        raise RuntimeError(f"UEFN: {body.get('error', '?')}")
    return body.get("result", {})


def _alive():
    """Check if UEFN bridge is running."""
    try:
        with urlopen(Request(_url()), timeout=5.0) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False

# ============================================================
# HTTP SERVER (receives pushes from UEFN)
# ============================================================

_incoming_queue = _queue_mod.Queue()


class _BlenderHTTPHandler(BaseHTTPRequestHandler):
    """Handle incoming commands from UEFN."""

    def do_GET(self):
        self._ok({"status": "ok", "port": _st.server_port})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception:
            self._err(400, "Bad JSON")
            return

        cmd = body.get("command", "")
        params = body.get("params", {})

        if cmd == "push_transforms":
            _incoming_queue.put(("push_transforms", params))
            self._ok({"status": "queued"})
        elif cmd == "ping":
            self._ok({"status": "ok", "port": _st.server_port})
        else:
            self._err(400, f"Unknown command: {cmd}")

    def _ok(self, data):
        b = json.dumps({"success": True, "result": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg):
        b = json.dumps({"success": False, "error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *args):
        pass


def _start_blender_server():
    """Start Blender's HTTP server for receiving UEFN pushes."""
    if _st._http_server:
        return _st.server_port
    for p in range(BLENDER_SERVER_PORT, BLENDER_MAX_PORT + 1):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            server = HTTPServer(("127.0.0.1", p), _BlenderHTTPHandler)
            _st._http_server = server
            _st.server_port = p
            _st._http_thread = threading.Thread(
                target=server.serve_forever, daemon=True)
            _st._http_thread.start()
            _log(f"Blender server on :{p}")
            return p
        except OSError:
            continue
    _err("Could not start Blender HTTP server (ports 8791-8795 busy)")
    return 0


def _stop_blender_server():
    """Stop Blender's HTTP server."""
    if _st._http_server:
        _st._http_server.shutdown()
        if _st._http_thread:
            _st._http_thread.join(timeout=3)
        _st._http_server = None
        _st._http_thread = None
        _st.server_port = 0
        _log("Blender server stopped")


def _apply_incoming_transforms(params):
    """Apply transforms received from UEFN. Reverse mapping: UE → Blender."""
    objects_data = params.get("objects", [])
    if not objects_data:
        return

    _st.receiving = True
    try:
        all_objs = {o.name: o for o in bpy.context.scene.objects
                    if o.type == "MESH"}
        updated = 0

        for od in objects_data:
            name = od.get("name", "")
            obj = all_objs.get(name)
            if not obj:
                continue

            ue_loc = od.get("location", [0, 0, 0])
            ue_rot = od.get("rotation", [0, 0, 0])  # [Pitch, Yaw, Roll]
            ue_scale = od.get("scale", [1, 1, 1])

            # Reverse UE → Blender into a WORLD matrix (single-source convention).
            # matrix_world handles parenting (parent-inverse) and any rotation_mode
            # (euler / quaternion / axis-angle) correctly — unlike writing local fields.
            loc = mathutils.Vector(coords.loc_ue_to_bl(*ue_loc))
            eul = mathutils.Euler(coords.rot_ue_to_bl(*ue_rot), "XYZ")
            scl = mathutils.Vector((ue_scale[0], ue_scale[1], ue_scale[2]))
            obj.matrix_world = mathutils.Matrix.LocRotScale(loc, eul, scl)
            updated += 1

        # Update snapshot to prevent re-sync loop
        _st.last_obj_hashes = _snapshot_all()
        _st.last_object_names = set(_st.last_obj_hashes.keys())
        _log(f"Received {updated} transform(s) from UEFN")
        _st.status = f"UEFN → Blender: {updated} updated"

    finally:
        _st.receiving = False


def _process_incoming():
    """Process incoming commands from UEFN (main thread timer)."""
    try:
        while not _incoming_queue.empty():
            cmd, params = _incoming_queue.get_nowait()
            if cmd == "push_transforms":
                _apply_incoming_transforms(params)
    except Exception as e:
        _err(f"Incoming process error: {e}")
    return 0.1  # re-run every 100ms


# ============================================================
# SCENE HASHING
# ============================================================


def _obj_geometry_hash(obj):
    """Hash a single object's mesh geometry (vertices + polygons)."""
    h = hashlib.md5()
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eo = obj.evaluated_get(depsgraph)
        mesh = eo.to_mesh()
        vc = len(mesh.vertices)
        h.update(struct.pack("ii", vc, len(mesh.polygons)))
        if vc <= 5000:
            for v in mesh.vertices:
                h.update(struct.pack("3f", v.co.x, v.co.y, v.co.z))
        else:
            step = max(1, vc // 500)
            for i in range(0, vc, step):
                h.update(struct.pack("3f",
                         mesh.vertices[i].co.x,
                         mesh.vertices[i].co.y,
                         mesh.vertices[i].co.z))
        eo.to_mesh_clear()
    except Exception:
        pass
    return h.hexdigest()


def _obj_material_hash(obj):
    """Hash a single object's material properties (excludes textures)."""
    h = hashlib.md5()
    if hasattr(obj.data, "materials"):
        for mat in obj.data.materials:
            if not mat:
                continue
            h.update(struct.pack("4f", *mat.diffuse_color))
            h.update(struct.pack("2f", mat.metallic, mat.roughness))
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.bl_idname == "ShaderNodeBsdfPrincipled":
                        for inp in node.inputs:
                            if hasattr(inp, "default_value"):
                                try:
                                    if hasattr(inp.default_value, "__iter__"):
                                        for val in inp.default_value:
                                            h.update(struct.pack("f", round(float(val), 4)))
                                    else:
                                        h.update(struct.pack("f", round(float(inp.default_value), 4)))
                                except Exception:
                                    pass
    return h.hexdigest()


def _obj_texture_hash(obj):
    """Hash a single object's texture image references."""
    h = hashlib.md5()
    if hasattr(obj.data, "materials"):
        for mat in obj.data.materials:
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    h.update(node.image.name.encode())
                    if node.image.filepath:
                        h.update(node.image.filepath.encode())
    return h.hexdigest()


def _obj_transform_hash(obj):
    """Hash a single object's WORLD transform (location, rotation, scale) + collection.
    Reads matrix_world (the SAME source as _obj_data) so the diff detects exactly what gets
    sent — including parent-driven changes. All three are actor-applied (data-driven), so any
    transform change is a FAST update — no re-export."""
    h = hashlib.md5()
    loc, rot_q, sc = obj.matrix_world.decompose()
    rot = rot_q.to_euler("XYZ")
    h.update(struct.pack("9f", loc.x, loc.y, loc.z, rot.x, rot.y, rot.z, sc.x, sc.y, sc.z))
    h.update(_get_collection_path(obj).encode())
    return h.hexdigest()


def _snapshot_all():
    """Snapshot all mesh objects' hashes for diff comparison."""
    # Flush the depsgraph so matrix_world reflects edits made in the N-panel/viewport.
    # Without this, matrix_world stays STALE until Blender flushes on its own (a save forces
    # it) — which is why a rotate wasn't picked up by Send Changes until the user saved first.
    bpy.context.view_layer.update()
    snap = {}
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        snap[obj.name] = {
            "geo": _obj_geometry_hash(obj),
            "mat": _obj_material_hash(obj),
            "tex": _obj_texture_hash(obj),
            "trans": _obj_transform_hash(obj),
        }
    return snap


def _compute_diff(old_snap, new_snap):
    """Compare two snapshots and categorize changes."""
    old_names = set(old_snap.keys())
    new_names = set(new_snap.keys())

    diff = {
        "added": list(new_names - old_names),
        "removed": list(old_names - new_names),
        "geo_changed": [],
        "mat_changed": [],
        "tex_changed": [],
        "trans_changed": [],
    }

    for name in old_names & new_names:
        old, new = old_snap[name], new_snap[name]
        if old["geo"] != new["geo"]:
            diff["geo_changed"].append(name)
        elif old["mat"] != new["mat"]:
            diff["mat_changed"].append(name)
        elif old["tex"] != new["tex"]:
            diff["tex_changed"].append(name)
        elif old["trans"] != new["trans"]:
            diff["trans_changed"].append(name)

    return diff

# ============================================================
# MATERIAL BACKUP / RESTORE
# ============================================================


def _backup_material(mat):
    """Save material node tree state for later restoration."""
    if not mat.use_nodes or not mat.node_tree:
        return None

    backup = {"nodes": [], "links": []}

    for node in mat.node_tree.nodes:
        nd = {
            "bl_idname": node.bl_idname,
            "name": node.name,
            "location": (node.location.x, node.location.y),
            "inputs": {},
        }
        for name, inp in node.inputs.items():
            if hasattr(inp, "default_value"):
                try:
                    nd["inputs"][name] = (
                        list(inp.default_value)
                        if hasattr(inp.default_value, "__iter__")
                        else inp.default_value
                    )
                except Exception:
                    pass
        if node.type == "TEX_IMAGE" and node.image:
            nd["image"] = node.image.name
            nd["colorspace"] = node.image.colorspace_settings.name
        backup["nodes"].append(nd)

    for link in mat.node_tree.links:
        backup["links"].append({
            "from_node": link.from_node.name,
            "from_socket": link.from_socket.name,
            "to_node": link.to_node.name,
            "to_socket": link.to_socket.name,
        })

    return backup


def _restore_material(mat, backup):
    """Restore material from backup."""
    tree = mat.node_tree
    tree.nodes.clear()
    nodes_map = {}

    for nd in backup["nodes"]:
        node = tree.nodes.new(nd["bl_idname"])
        node.name = nd["name"]
        node.location = nd["location"]
        nodes_map[nd["name"]] = node

        for name, val in nd["inputs"].items():
            if name in node.inputs and hasattr(node.inputs[name], "default_value"):
                try:
                    if isinstance(val, list):
                        for i in range(min(len(val), len(node.inputs[name].default_value))):
                            node.inputs[name].default_value[i] = type(
                                node.inputs[name].default_value[i])(val[i])
                    else:
                        node.inputs[name].default_value = type(
                            node.inputs[name].default_value)(val)
                except Exception:
                    pass

        if "image" in nd and nd["image"] in bpy.data.images:
            node.image = bpy.data.images[nd["image"]]
            if "colorspace" in nd:
                node.image.colorspace_settings.name = nd["colorspace"]

    for ld in backup["links"]:
        try:
            fn = nodes_map.get(ld["from_node"])
            tn = nodes_map.get(ld["to_node"])
            if fn and tn:
                tree.links.new(fn.outputs[ld["from_socket"]],
                               tn.inputs[ld["to_socket"]])
        except Exception:
            pass


def _restore_baked(backups):
    """Restore all baked materials."""
    for mat_name, backup in backups.items():
        if mat_name in bpy.data.materials:
            _restore_material(bpy.data.materials[mat_name], backup)

# ============================================================
# BAKING
# ============================================================


def _simplify_material(mat, textures):
    """Replace material with simple textured Principled BSDF."""
    tree = mat.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    x, y = -400, 300
    for ch, filepath in textures.items():
        img_name = os.path.basename(filepath)
        img = bpy.data.images.get(img_name) or bpy.data.images.load(filepath)

        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (x, y)

        if ch == "base_color":
            img.colorspace_settings.name = "sRGB"
            tree.links.new(tex.outputs["Color"], principled.inputs["Base Color"])
        elif ch == "roughness":
            img.colorspace_settings.name = "Non-Color"
            tree.links.new(tex.outputs["Color"], principled.inputs["Roughness"])
        elif ch == "metallic":
            img.colorspace_settings.name = "Non-Color"
            tree.links.new(tex.outputs["Color"], principled.inputs["Metallic"])
        elif ch == "normal":
            img.colorspace_settings.name = "Non-Color"
            nm = tree.nodes.new("ShaderNodeNormalMap")
            nm.location = (x + 200, y)
            tree.links.new(tex.outputs["Color"], nm.inputs["Color"])
            tree.links.new(nm.outputs["Normal"], principled.inputs["Normal"])

        y -= 300


def _bake_channel(obj, mat, tree, principled, ch_name, bake_type,
                  is_color, resolution, tex_dir):
    """Bake a single channel. Returns file path or None."""
    tex_name = f"{obj.name}_{mat.name}_{ch_name}".replace(" ", "_")
    img = bpy.data.images.new(tex_name, resolution, resolution)
    img.colorspace_settings.name = "sRGB" if is_color else "Non-Color"

    img_node = tree.nodes.new("ShaderNodeTexImage")
    img_node.image = img
    tree.nodes.active = img_node

    filepath = None
    try:
        if bake_type == "DIFFUSE":
            bpy.context.scene.render.bake.use_pass_direct = False
            bpy.context.scene.render.bake.use_pass_indirect = False
            bpy.context.scene.render.bake.use_pass_color = True

        if bake_type == "_METALLIC":
            # Emission trick: route metallic value through emission for baking
            output_node = None
            for n in tree.nodes:
                if n.type == "OUTPUT_MATERIAL":
                    output_node = n
                    break

            if output_node and principled:
                emit = tree.nodes.new("ShaderNodeEmission")
                emit.name = "_bake_emit_tmp"

                met_input = principled.inputs.get("Metallic")
                if met_input and met_input.links:
                    src = met_input.links[0].from_socket
                    tree.links.new(src, emit.inputs["Color"])
                else:
                    val = met_input.default_value if met_input else 0.0
                    emit.inputs["Color"].default_value = (val, val, val, 1.0)

                old_link_src = None
                for link in output_node.inputs["Surface"].links:
                    old_link_src = link.from_socket
                tree.links.new(emit.outputs["Emission"],
                               output_node.inputs["Surface"])

                bpy.ops.object.bake(type="EMIT")

                tree.nodes.remove(emit)
                if old_link_src:
                    tree.links.new(old_link_src, output_node.inputs["Surface"])
        else:
            bpy.ops.object.bake(type=bake_type)

        fp = os.path.join(tex_dir, f"{tex_name}.png")
        img.filepath_raw = fp
        img.file_format = "PNG"
        img.save()
        filepath = fp

    except Exception as e:
        _err(f"Bake {ch_name} failed for {obj.name}: {e}")

    tree.nodes.remove(img_node)
    bpy.data.images.remove(img)
    return filepath


def bake_and_simplify(target_objects, resolution=1024, bake_metallic=True):
    """Bake textures from complex materials, simplify, return backups."""
    tex_dir = os.path.join(EXCHANGE_DIR, "textures")
    os.makedirs(tex_dir, exist_ok=True)

    scene = bpy.context.scene
    orig_engine = scene.render.engine
    orig_active = bpy.context.view_layer.objects.active
    orig_selected = [o for o in bpy.context.selected_objects]

    backups = {}

    channels = [
        ("base_color", "DIFFUSE", True),
        ("roughness", "ROUGHNESS", False),
        ("normal", "NORMAL", False),
    ]
    if bake_metallic:
        channels.append(("metallic", "_METALLIC", False))

    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
        scene.cycles.device = "CPU"

        for obj in target_objects:
            if obj.type != "MESH" or not obj.data.materials:
                continue
            if not obj.data.uv_layers:
                _log(f"{obj.name}: no UVs, skipping bake")
                continue

            for mi, slot in enumerate(obj.material_slots):
                mat = slot.material
                if not mat or not mat.use_nodes:
                    continue

                tree = mat.node_tree
                principled = None
                for node in tree.nodes:
                    if node.bl_idname == "ShaderNodeBsdfPrincipled":
                        principled = node
                        break
                if not principled:
                    continue

                bc = principled.inputs.get("Base Color")
                if not bc or not bc.links:
                    continue

                if mat.name not in backups:
                    backups[mat.name] = _backup_material(mat)

                for o in scene.objects:
                    o.select_set(False)
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                obj.active_material_index = mi

                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                    bpy.ops.mesh.select_all(action="SELECT")
                    bpy.ops.uv.smart_project()
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    try:
                        bpy.ops.object.mode_set(mode="OBJECT")
                    except Exception:
                        pass

                textures = {}
                for ch_name, bake_type, is_color in channels:
                    fp = _bake_channel(
                        obj, mat, tree, principled,
                        ch_name, bake_type, is_color, resolution, tex_dir)
                    if fp:
                        textures[ch_name] = fp

                if textures:
                    _simplify_material(mat, textures)

    finally:
        scene.render.engine = orig_engine
        for o in scene.objects:
            o.select_set(o in set(orig_selected))
        bpy.context.view_layer.objects.active = orig_active

    return backups

# ============================================================
# EXCHANGE FOLDER + EXPORT
# ============================================================


def _clean_exchange():
    """Clean exchange folder."""
    os.makedirs(EXCHANGE_DIR, exist_ok=True)

    tex_dir = os.path.join(EXCHANGE_DIR, "textures")
    if os.path.isdir(tex_dir):
        for f in os.listdir(tex_dir):
            fp = os.path.join(tex_dir, f)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass

    for f in os.listdir(EXCHANGE_DIR):
        if f.endswith((".fbx", ".json")):
            try:
                os.remove(os.path.join(EXCHANGE_DIR, f))
            except Exception:
                pass


def _export_textures():
    """Export all textures used in scene materials to exchange folder."""
    tex_dir = os.path.join(EXCHANGE_DIR, "textures")
    os.makedirs(tex_dir, exist_ok=True)
    tex_paths = {}

    for mat in bpy.data.materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                img = node.image
                name = img.name
                fmt = img.file_format or "PNG"
                ext = fmt.lower().replace("jpeg", "jpg")
                filepath = os.path.join(tex_dir, f"{name}.{ext}")
                img.filepath_raw = filepath
                img.file_format = fmt
                try:
                    img.save()
                except Exception:
                    try:
                        img.pack()
                        img.save()
                    except Exception:
                        pass
                tex_paths[name] = filepath

    return tex_paths


def _export_textures_for_objects(obj_names):
    """Export textures used by specific objects only. Returns {image_name: path}."""
    tex_dir = os.path.join(EXCHANGE_DIR, "textures")
    os.makedirs(tex_dir, exist_ok=True)
    tex_paths = {}
    seen = set()

    all_objs = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}
    for name in obj_names:
        obj = all_objs.get(name)
        if not obj or not hasattr(obj.data, "materials"):
            continue
        for mat in obj.data.materials:
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue
            if mat.name in seen:
                continue
            seen.add(mat.name)
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    img = node.image
                    img_name = img.name
                    if img_name in tex_paths:
                        continue
                    fmt = img.file_format or "PNG"
                    ext = fmt.lower().replace("jpeg", "jpg")
                    filepath = os.path.join(tex_dir, f"{img_name}.{ext}")
                    img.filepath_raw = filepath
                    img.file_format = fmt
                    try:
                        img.save()
                    except Exception:
                        try:
                            img.pack()
                            img.save()
                        except Exception:
                            pass
                    tex_paths[img_name] = filepath

    return tex_paths


def _export_material_info_for_objects(obj_names, tex_paths):
    """Export material metadata JSON for specific objects. Returns path to JSON."""
    materials = []
    seen = set()

    all_objs = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}
    for obj_name in obj_names:
        obj = all_objs.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            mat_key = f"{obj_name}:{mat.name}"
            if mat_key in seen:
                continue
            seen.add(mat_key)

            mat_info = {
                "object_name": obj_name,
                "name": mat.name,
                "textures": {},
            }

            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image:
                        for out in node.outputs:
                            for link in out.links:
                                socket_name = link.to_socket.name.lower()
                                mat_info["textures"][socket_name] = {
                                    "name": node.image.name,
                                    "path": tex_paths.get(node.image.name, ""),
                                    "colorspace": node.image.colorspace_settings.name,
                                }

            materials.append(mat_info)

    mat_json = os.path.join(EXCHANGE_DIR, "materials.json")
    with open(mat_json, "w") as f:
        json.dump(materials, f)
    return mat_json


def _export_material_info(tex_paths):
    """Export material metadata to JSON."""
    materials = []

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue

            mat_info = {
                "name": mat.name,
                "diffuse_color": list(mat.diffuse_color),
                "metallic": mat.metallic,
                "roughness": mat.roughness,
                "specular": mat.specular_intensity,
                "textures": {},
            }

            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image:
                        for out in node.outputs:
                            for link in out.links:
                                socket_name = link.to_socket.name.lower()
                                mat_info["textures"][socket_name] = {
                                    "name": node.image.name,
                                    "path": tex_paths.get(node.image.name, ""),
                                    "colorspace": node.image.colorspace_settings.name,
                                }

                    if node.bl_idname == "ShaderNodeBsdfPrincipled":
                        for name, inp in node.inputs.items():
                            if hasattr(inp, "default_value"):
                                try:
                                    key = name.lower().replace(" ", "_")
                                    if hasattr(inp.default_value, "__iter__"):
                                        mat_info[key] = [
                                            round(float(v), 4)
                                            for v in inp.default_value
                                        ]
                                    else:
                                        mat_info[key] = round(
                                            float(inp.default_value), 4)
                                except Exception:
                                    pass

            materials.append(mat_info)

    mat_json = os.path.join(EXCHANGE_DIR, "materials.json")
    with open(mat_json, "w") as f:
        json.dump(materials, f)
    return mat_json


def _apply_rotation_scale(objects):
    """Apply rotation & scale to mesh objects (preserves user pivot)."""
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = [o for o in bpy.context.scene.objects if o.select_get()]
    prev_mode = bpy.context.mode
    if prev_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        if o.type == "MESH":
            o.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]

    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Restore selection
    bpy.ops.object.select_all(action="DESELECT")
    for o in prev_selected:
        o.select_set(True)
    bpy.context.view_layer.objects.active = prev_active


def _export_fbx(selected_only=False):
    """Export FBX. selected_only=True exports current selection only."""
    filepath = os.path.join(EXCHANGE_DIR, "scene.fbx")

    if not selected_only:
        for obj in bpy.context.scene.objects:
            obj.select_set(obj.type == "MESH")

    if not any(obj.select_get() for obj in bpy.context.scene.objects):
        return None

    # DATA-DRIVEN export: neutralize the WORLD matrix (parent + local) so the FBX carries the
    # PURE mesh data — no location, no rotation, no scale, no parent influence. The UEFN actor
    # supplies the full world transform (location + rotation + world-scale). UEFN reads FBX
    # numbers as METERS, so global_scale=1 makes m->cm automatic; the actor's world-scale then
    # gives the correct size in ANY case (parented, unparented, scaled). Zeroing only the LOCAL
    # transform was wrong: a parent's scale leaked into the geometry (100x gigantic once the
    # parent was cleared and that scale folded into local).
    selected_meshes = [o for o in bpy.context.scene.objects
                       if o.select_get() and o.type == "MESH"]
    # Instancing (F-44): export only ONE object per unique mesh datablock (the representative);
    # linked duplicates reference it via their "mesh" field and become extra actors in UEFN.
    rep_names = set(_mesh_rep_map(selected_meshes).values())
    for o in selected_meshes:
        if o.name not in rep_names:
            o.select_set(False)
    selected_meshes = [o for o in selected_meshes if o.name in rep_names]
    # Keep a SELECTED object active — deselecting non-reps can orphan the active object, which
    # makes the FBX exporter produce an empty file on some selections.
    if selected_meshes:
        bpy.context.view_layer.objects.active = selected_meshes[0]
    saved = {}
    for o in selected_meshes:
        saved[o.name] = (o.location.copy(), o.rotation_euler.copy(),
                         o.rotation_quaternion.copy(), o.scale.copy(), o.rotation_mode)
        o.matrix_world = mathutils.Matrix.Identity(4)
    bpy.context.view_layer.update()

    def _restore():
        for o in selected_meshes:
            if o.name in saved:
                loc, eul, quat, scl, mode = saved[o.name]
                o.location, o.rotation_euler, o.rotation_quaternion, o.scale = loc, eul, quat, scl
                o.rotation_mode = mode

    try:
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                with bpy.context.temp_override(area=area):
                    bpy.ops.export_scene.fbx(
                        filepath=filepath,
                        use_selection=True,
                        global_scale=1.0,             # UEFN reads FBX as meters -> m->cm automatic
                        apply_unit_scale=False,
                        apply_scale_options="FBX_SCALE_NONE",
                        axis_forward="X", axis_up="Z",  # bake Z-up INTO the FBX (stands upright)
                        use_space_transform=True,
                        bake_space_transform=False,
                        mesh_smooth_type="FACE",
                        use_mesh_modifiers=True,
                        add_leaf_bones=False,
                        path_mode="COPY",
                        embed_textures=True,
                    )
                _restore()
                return filepath
    finally:
        _restore()

    return None


def _export_fbx_objects(obj_names):
    """Export individual FBX files per object. Returns {obj_name: fbx_path}."""
    results = {}
    area_3d = None
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            area_3d = area
            break
    if not area_3d:
        return results

    all_objs = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}

    # Instancing (F-44): export one FBX per unique mesh datablock (the representative); the other
    # sharers reference it via their "mesh" field in _obj_data and become extra actors in UEFN.
    sel_objs = [all_objs[n] for n in obj_names if n in all_objs]
    rep_names = sorted(set(_mesh_rep_map(sel_objs).values()))

    for name in rep_names:
        obj = all_objs.get(name)
        if not obj:
            continue

        # Sanitize filename (dots/spaces to underscores)
        safe_name = name.replace(".", "_").replace(" ", "_")
        filepath = os.path.join(EXCHANGE_DIR, f"{safe_name}.fbx")

        # Select only this object
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        # DATA-DRIVEN: neutralize the WORLD matrix (parent + local) so the FBX carries the PURE
        # mesh data; the actor carries the full world location/rotation/world-scale. UEFN reads
        # FBX as METERS, so global_scale=1 makes m->cm automatic and the actor's world-scale
        # sizes it correctly in any case (parented, unparented, scaled). See _export_fbx.
        saved = (obj.location.copy(), obj.rotation_euler.copy(),
                 obj.rotation_quaternion.copy(), obj.scale.copy(), obj.rotation_mode)
        obj.matrix_world = mathutils.Matrix.Identity(4)
        bpy.context.view_layer.update()

        try:
            with bpy.context.temp_override(area=area_3d):
                bpy.ops.export_scene.fbx(
                    filepath=filepath,
                    use_selection=True,
                    global_scale=1.0,             # UEFN reads FBX as meters -> m->cm automatic
                    apply_unit_scale=False,
                    apply_scale_options="FBX_SCALE_NONE",
                    axis_forward="X", axis_up="Z",  # bake Z-up INTO the FBX (stands upright)
                    use_space_transform=True,
                    bake_space_transform=False,
                    mesh_smooth_type="FACE",
                    use_mesh_modifiers=True,
                    add_leaf_bones=False,
                    path_mode="COPY",
                    embed_textures=True,
                )
            results[name] = filepath
        except Exception as e:
            _err(f"FBX export failed for {name}: {e}")
        finally:
            obj.location, obj.rotation_euler, obj.rotation_quaternion, obj.scale, obj.rotation_mode = saved
            bpy.context.view_layer.update()

    return results


def _collect_material_data(objects):
    """Extract material property data for the given objects."""
    materials = []
    for o in objects:
        if not hasattr(o.data, "materials"):
            continue
        for mat in o.data.materials:
            if not mat:
                continue
            mat_info = {
                "object_name": o.name,
                "name": mat.name,
                "diffuse_color": list(mat.diffuse_color),
                "metallic": mat.metallic,
                "roughness": mat.roughness,
            }
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.bl_idname == "ShaderNodeBsdfPrincipled":
                        for inp in node.inputs:
                            if hasattr(inp, "default_value"):
                                try:
                                    if inp.name == "Base Color":
                                        mat_info["base_color"] = list(inp.default_value)
                                    elif inp.name == "Metallic":
                                        mat_info["metallic"] = float(inp.default_value)
                                    elif inp.name == "Roughness":
                                        mat_info["roughness"] = float(inp.default_value)
                                    elif inp.name == "Specular IOR Level":
                                        mat_info["specular"] = float(inp.default_value)
                                except Exception:
                                    pass
            materials.append(mat_info)
    return materials


# ============================================================
# MESH HELPERS
# ============================================================


def _get_mesh_children(obj):
    """Recursively get all mesh children."""
    result = []
    for child in obj.children:
        if child.type == "MESH":
            result.append(child)
        result.extend(_get_mesh_children(child))
    return result


def _collect_selected_meshes():
    """Get selected mesh objects including children."""
    meshes = set()
    for obj in bpy.context.selected_objects:
        if obj.type == "MESH":
            meshes.add(obj)
        meshes.update(_get_mesh_children(obj))
    return list(meshes)


def _get_collection_path(obj):
    """Get the deepest collection path for an object, excluding Scene Collection.
    Returns e.g. 'Buildings/Residential' or '' if only in Scene Collection root.
    """
    def _find(col, path):
        results = []
        for child in col.children:
            child_path = f"{path}/{child.name}" if path else child.name
            if obj.name in child.objects:
                results.append(child_path)
            results.extend(_find(child, child_path))
        return results

    paths = _find(bpy.context.scene.collection, "")
    if not paths:
        return ""
    return max(paths, key=lambda p: p.count("/"))


def _dedup_guids():
    """Ensure every mesh object has a UNIQUE bb_guid. Blender COPIES custom properties on
    duplicate (Shift+D / Alt+D), so duplicates inherit the source's bb_guid and would all
    collapse onto the SAME UEFN actor (the bridge matches actors by GUID). The first holder
    (by name) keeps the guid; every later collider gets a fresh one (persisted = stable)."""
    seen = set()
    for o in sorted((ob for ob in bpy.context.scene.objects if ob.type == "MESH"),
                    key=lambda x: x.name):
        g = o.get("bb_guid")
        if g and g in seen:
            g = uuid.uuid4().hex
            o["bb_guid"] = g
        if g:
            seen.add(g)


def _mesh_rep_map(objects):
    """Map each mesh datablock name -> its REPRESENTATIVE object name (min name) among `objects`.
    Objects sharing a datablock (linked duplicates) resolve to one representative, so they export
    ONE mesh and spawn N actors in UEFN (instancing — F-44)."""
    rep = {}
    for o in objects:
        if o.type != "MESH" or not o.data:
            continue
        key = o.data.name
        if key not in rep or o.name < rep[key]:
            rep[key] = o.name
    return rep


def _obj_data(objects):
    """Build object data list with Blender→UE coordinate conversion.

    Uses matrix_world.decompose() to get the TRUE world transform,
    handling parents, constraints, and delta transforms correctly.

    Blender: X=right, Y=forward, Z=up (right-handed, meters)
    UE/UEFN: X=forward, Y=right, Z=up (left-handed, centimeters)

    The FBX pipeline handles axis reorientation for mesh vertices.
    World coordinates only need the handedness flip (negate Y).
    The convention lives in coords.py (single source, unit-tested).
    """
    # Flush the depsgraph so matrix_world is current (see _snapshot_all) — guarantees we send
    # the just-edited transform, not a stale one, on the send_selected/send_scene paths too.
    bpy.context.view_layer.update()
    _dedup_guids()                  # duplicates (Alt+D/Shift+D) inherit bb_guid -> give fresh ones
    reps = _mesh_rep_map(objects)   # instancing (F-44): shared datablock -> one representative
    result = []
    for o in objects:
        mw = o.matrix_world.copy()
        loc, rot_q, sc = mw.decompose()
        rot = rot_q.to_euler('XYZ')

        # Stable identity that survives rename/duplicate (B3): a GUID stored as a custom
        # property on the object, mirrored to a tag on the UEFN actor.
        guid = o.get("bb_guid")
        if not guid:
            guid = uuid.uuid4().hex
            o["bb_guid"] = guid

        result.append({
            "name": o.name,
            "guid": guid,
            # Representative mesh name for this object's datablock — instances share it (F-44).
            "mesh": reps.get(o.data.name, o.name) if o.data else o.name,
            "collection": _get_collection_path(o),
            "location": coords.loc_bl_to_ue(loc.x, loc.y, loc.z),
            # DATA-DRIVEN: mesh is exported clean (no baked rotation), the actor carries the
            # full world rotation. This conversion is the CALIBRATION TARGET (coords.py).
            "rotation": coords.rot_bl_to_ue(rot.x, rot.y, rot.z),
            "scale": [sc.x, sc.y, sc.z],
        })
    return result

# ============================================================
# SEND COMMANDS
# ============================================================


def _blend_project_name():
    """Project folder name = the .blend filename (no extension). Auto-identifies the Blender
    project — no typing — and keeps each .blend's assets in its own UEFN subfolder. '' if unsaved."""
    path = bpy.data.filepath
    return os.path.splitext(os.path.basename(path))[0] if path else ""


def do_connect():
    """Connect to UEFN bridge."""
    props = bpy.context.scene.uefn_bridge
    # Auto: the .blend filename. Optional manual override via the panel field. Never blocks.
    pname = props.project_name.strip() or _blend_project_name() or "Untitled"
    if pname == "Untitled":
        _log("Unsaved .blend — using 'Untitled'. Save the file to name the project folder.",
             "warning")

    _st.status = "Connecting..."
    _log(f"Connecting to UEFN at {_url()}...")

    # Start Blender HTTP server for bidirectional sync
    if not _st.server_port:
        _start_blender_server()

    if not _alive():
        _st.status = "UEFN not reachable"
        _err("Cannot reach UEFN bridge.\n"
             "  1. Open UEFN project\n"
             "  2. Tools > Execute Python Script > uefn_blender_bridge.py\n"
             "  3. Dashboard opens, then Connect here")
        return False

    try:
        r = _send("blender_connect", {
            "blender_version": bpy.app.version_string,
            "addon_version": ADDON_VERSION,
            "project_name": pname,
            "server_port": _st.server_port,
        })
        _st.connected = True
        _st.project_path = r.get("project_path", "")
        _st.project_name = pname
        _st.status = "Connected"
        _log(f"Connected! Project: {_st.project_path} / {pname}")

        # Start incoming queue timer
        if not bpy.app.timers.is_registered(_process_incoming):
            bpy.app.timers.register(_process_incoming, first_interval=0.5,
                                    persistent=True)

        # If Live Sync was already on (e.g. reconnecting after re-running the UEFN script),
        # re-assert it so UEFN gets our port + live flag back.
        if _st.live_active:
            try:
                _send("set_live_sync", {"active": True, "blender_port": _st.server_port})
            except Exception:
                pass

        return True
    except Exception as e:
        _st.status = f"Failed: {e}"
        _err(f"Connect failed: {e}")
        return False


def do_disconnect():
    """Disconnect from UEFN bridge."""
    if _st.live_active:
        do_stop_live()
    if _st.connected:
        try:
            _send("blender_disconnect")
        except Exception:
            pass
    _st.connected = False
    _st.status = "Disconnected"
    _st.project_path = ""
    # Stop incoming timer
    if bpy.app.timers.is_registered(_process_incoming):
        bpy.app.timers.unregister(_process_incoming)
    _log("Disconnected")


def do_send_scene(is_live=False):
    """Export full scene and send to UEFN."""
    if not _st.connected:
        return False

    _st.status = "Live sync..." if is_live else "Exporting..."

    # Capture world transforms BEFORE export (export may modify them)
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    obj_payload = _obj_data(objects)

    _clean_exchange()
    tex_paths = _export_textures()
    _export_material_info(tex_paths)
    fbx_path = _export_fbx()

    if not fbx_path:
        _st.status = "No meshes to export"
        return False

    combine = bpy.context.scene.uefn_bridge.combine_meshes
    params = {
        "fbx_path": fbx_path,
        "scene_name": bpy.context.scene.name,
        "object_count": len(objects),
        "objects": obj_payload,
        "exchange_folder": EXCHANGE_DIR,
        "combine_meshes": combine,
    }

    try:
        r = _send("import_scene", params)
        _st.last_obj_hashes = _snapshot_all()
        _st.last_object_names = set(_st.last_obj_hashes.keys())
        _st.last_send_time = time.time()
        _st.send_count += 1
        ac = r.get("actors", 0)
        _st.status = f"Live: {ac} actors" if is_live else f"Sent: {ac} actors"
        _log(f"Scene sent: {ac} actors, {r.get('materials', 0)} materials")
        return True
    except Exception as e:
        _st.status = f"Send failed: {e}"
        _err(f"Send scene error: {e}")
        return False


def do_send_selected():
    """Export selected objects and send to UEFN."""
    if not _st.connected:
        return False

    selected = _collect_selected_meshes()
    if not selected:
        _st.status = "No mesh selected"
        return False

    _st.status = "Exporting selected..."

    # Capture world transforms BEFORE export (export may modify them)
    obj_payload = _obj_data(selected)

    # Scope textures + material metadata to the SELECTION (B4) — not the whole scene.
    obj_names = [o.name for o in selected]
    _clean_exchange()
    tex_paths = _export_textures_for_objects(obj_names)
    _export_material_info_for_objects(obj_names, tex_paths)

    orig_mode = bpy.context.mode
    if orig_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    for obj in bpy.context.scene.objects:
        obj.select_set(False)
    for obj in selected:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = selected[0]

    fbx_path = _export_fbx(selected_only=True)
    if not fbx_path:
        _st.status = "Export failed"
        return False

    combine = bpy.context.scene.uefn_bridge.combine_meshes
    params = {
        "fbx_path": fbx_path,
        "scene_name": "_".join([o.name for o in selected]),
        "object_count": len(selected),
        "objects": obj_payload,
        "exchange_folder": EXCHANGE_DIR,
        "selected_only": True,
        "combine_meshes": combine,
    }

    try:
        r = _send("import_scene", params)
        _st.send_count += 1
        ac = r.get("actors", 0)
        _st.status = f"Sent {ac} selected"
        _log(f"Selected sent: {ac} actors")
        return True
    except Exception as e:
        _st.status = f"Send failed: {e}"
        _err(f"Send selected error: {e}")
        return False


def do_send_changes():
    """Smart diff sync — detects what changed and syncs only that."""
    if not _st.connected:
        return False

    new_snap = _snapshot_all()
    diff = _compute_diff(_st.last_obj_hashes, new_snap)

    has_changes = any([diff["added"], diff["removed"], diff["geo_changed"],
                       diff["mat_changed"], diff["tex_changed"], diff["trans_changed"]])
    if not has_changes:
        _st.status = "No changes detected"
        _log("No changes")
        return False

    all_objs = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}
    scene_name = bpy.context.scene.name
    _clean_exchange()
    success = True

    try:
        # Category 5: Removed objects
        if diff["removed"]:
            _log(f"Removing {len(diff['removed'])} object(s): {diff['removed']}")
            _send("remove_objects", {"names": diff["removed"]})

        # Category 5: Added objects
        if diff["added"]:
            added = [all_objs[n] for n in diff["added"] if n in all_objs]
            obj_payload = _obj_data(added)
            fbx_paths = _export_fbx_objects(diff["added"])
            if fbx_paths:
                _log(f"Adding {len(fbx_paths)} object(s)")
                _send("add_objects", {
                    "fbx_paths": fbx_paths,
                    "objects": obj_payload,
                    "scene_name": scene_name,
                    "exchange_folder": EXCHANGE_DIR,
                })

        # Category 2: Geometry changed (FBX re-export per object)
        if diff["geo_changed"]:
            geo_objs = [all_objs[n] for n in diff["geo_changed"] if n in all_objs]
            obj_payload = _obj_data(geo_objs)
            fbx_paths = _export_fbx_objects(diff["geo_changed"])
            if fbx_paths:
                _log(f"Updating geometry for {len(fbx_paths)} object(s)")
                _send("update_objects", {
                    "fbx_paths": fbx_paths,
                    "objects": obj_payload,
                    "scene_name": scene_name,
                    "exchange_folder": EXCHANGE_DIR,
                })

        # Category 4: Texture changed (no FBX — just texture files + material rebind)
        if diff["tex_changed"]:
            tex_paths = _export_textures_for_objects(diff["tex_changed"])
            _export_material_info_for_objects(diff["tex_changed"], tex_paths)
            _log(f"Updating textures for {len(diff['tex_changed'])} object(s)")
            _send("update_textures", {
                "object_names": diff["tex_changed"],
                "exchange_folder": EXCHANGE_DIR,
                "scene_name": scene_name,
            })

        # Category 3: Material properties changed
        if diff["mat_changed"]:
            mat_objs = [all_objs[n] for n in diff["mat_changed"] if n in all_objs]
            mat_data = _collect_material_data(mat_objs)
            if mat_data:
                _log(f"Updating materials for {len(diff['mat_changed'])} object(s)")
                _send("update_materials", {"materials": mat_data})

        # Category 1: Transform only
        if diff["trans_changed"]:
            trans_objs = [all_objs[n] for n in diff["trans_changed"] if n in all_objs]
            _log(f"Updating transforms for {len(diff['trans_changed'])} object(s)")
            _send("update_transforms", {"objects": _obj_data(trans_objs)})

    except Exception as e:
        _st.status = f"Sync failed: {e}"
        _err(f"Smart sync error: {e}")
        success = False

    # Update baseline snapshot
    _st.last_obj_hashes = new_snap
    _st.last_object_names = set(new_snap.keys())
    _st.last_send_time = time.time()
    _st.send_count += 1

    # Build status message
    parts = []
    if diff["added"]: parts.append(f"+{len(diff['added'])}")
    if diff["removed"]: parts.append(f"-{len(diff['removed'])}")
    if diff["geo_changed"]: parts.append(f"geo:{len(diff['geo_changed'])}")
    if diff["mat_changed"]: parts.append(f"mat:{len(diff['mat_changed'])}")
    if diff["tex_changed"]: parts.append(f"tex:{len(diff['tex_changed'])}")
    if diff["trans_changed"]: parts.append(f"pos:{len(diff['trans_changed'])}")
    _st.status = f"Synced: {', '.join(parts)}"
    _log(f"Smart sync: {', '.join(parts)}")
    return success


def do_update_transforms():
    """Send only transform updates (no FBX re-export)."""
    if not _st.connected:
        return False

    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    try:
        r = _send("update_transforms", {"objects": _obj_data(objects)})
        _st.last_obj_hashes = _snapshot_all()
        _st.last_object_names = set(_st.last_obj_hashes.keys())
        _st.last_send_time = time.time()
        u = r.get("updated", 0)
        _st.status = f"Transforms: {u} updated"
        _log(f"Transforms updated: {u}")
        return True
    except Exception as e:
        _st.status = f"Transform failed: {e}"
        _err(f"Transform update error: {e}")
        return False


def do_bake_and_send(selected_only=False):
    """Bake textures, export, and send to UEFN."""
    if not _st.connected:
        return False

    if selected_only:
        targets = _collect_selected_meshes()
        if not targets:
            _st.status = "No mesh selected"
            return False
    else:
        targets = [o for o in bpy.context.scene.objects if o.type == "MESH"]

    if not targets:
        _st.status = "No meshes"
        return False

    _st.status = "Baking..."
    _clean_exchange()

    try:
        props = bpy.context.scene.uefn_bridge
        resolution = props.bake_resolution
        bake_met = props.bake_metallic
    except Exception:
        resolution = 1024
        bake_met = True

    try:
        backups = bake_and_simplify(targets, resolution, bake_met)
    except Exception as e:
        _err(f"Bake error: {e}")
        backups = {}

    _export_textures()

    if selected_only:
        for obj in bpy.context.scene.objects:
            obj.select_set(False)
        for obj in targets:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = targets[0]
        fbx_path = _export_fbx(selected_only=True)
    else:
        fbx_path = _export_fbx()

    if backups:
        _restore_baked(backups)

    if not fbx_path:
        _st.status = "Export failed"
        return False

    combine = bpy.context.scene.uefn_bridge.combine_meshes
    params = {
        "fbx_path": fbx_path,
        "scene_name": bpy.context.scene.name,
        "object_count": len(targets),
        "objects": _obj_data(targets),
        "exchange_folder": EXCHANGE_DIR,
        "combine_meshes": combine,
    }

    try:
        r = _send("import_baked", params)
        _st.send_count += 1
        ac = r.get("actors", 0)
        _st.status = f"Baked: {ac} actors"
        _log(f"Baked & sent: {ac} actors, {r.get('materials', 0)} materials")
        return True
    except Exception as e:
        _st.status = f"Bake send failed: {e}"
        _err(f"Bake send error: {e}")
        return False


def do_clean_all():
    """Request UEFN to clean all imported assets."""
    if not _st.connected:
        return False
    try:
        r = _send("clean_all", {"confirm": True})
        c = r.get("cleaned", 0)
        _st.status = f"Cleaned {c} actors"
        _log(f"Clean all: {c} actors")
        return True
    except Exception as e:
        _st.status = f"Clean failed: {e}"
        return False

# ============================================================
# LIVE SYNC (triggers on Ctrl+S)
# ============================================================


@persistent
def _on_save(dummy):
    """Called after every Ctrl+S — syncs to UEFN if live mode is active."""
    if not _st.live_active or not _st.connected:
        return
    # Don't sync back when applying inbound changes from UEFN
    if _st.receiving:
        _log("Skipping outbound sync — applying inbound changes")
        return
    # Block sync in Edit Mode — FBX export requires Object Mode
    if bpy.context.mode != "OBJECT":
        _log("Skipping sync — not in Object Mode (exit Edit Mode first)")
        return
    try:
        _log("Save detected — syncing to UEFN...")
        do_send_changes()
    except Exception as e:
        _err(f"Save sync error: {e}")


def do_start_live():
    """Start live sync — auto-syncs on every Ctrl+S."""
    if _st.live_active:
        return
    _st.live_active = True
    _st.last_obj_hashes = _snapshot_all()
    _st.last_object_names = set(_st.last_obj_hashes.keys())
    if _on_save not in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(_on_save)
    # Tell UEFN to start pushing actor edits back to us (bidirectional live sync). Send our
    # server port too, so this re-establishes it even if the UEFN script was re-run.
    try:
        _send("set_live_sync", {"active": True, "blender_port": _st.server_port})
    except Exception as e:
        _err(f"Could not enable UEFN->Blender live sync: {e}")
    _st.status = "Live: Blender⇄UEFN"
    _log("Live sync started (Blender→UEFN on save; UEFN→Blender live)")


def do_stop_live():
    """Stop live sync."""
    _st.live_active = False
    if _on_save in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_on_save)
    try:
        _send("set_live_sync", {"active": False})
    except Exception:
        pass
    _st.status = "Connected"
    _log("Live sync stopped")

# ============================================================
# OPERATORS
# ============================================================


class UEFNBRIDGE_OT_connect(bpy.types.Operator):
    bl_idname = "uefn_bridge.connect"
    bl_label = "Connect to UEFN"
    bl_description = "Connect to UEFN bridge server"

    def execute(self, context):
        props = context.scene.uefn_bridge
        _st.host = props.host
        _st.port = props.port
        if do_connect():
            self.report({"INFO"}, f"Connected to UEFN ({_st.project_path})")
        else:
            self.report({"WARNING"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_disconnect(bpy.types.Operator):
    bl_idname = "uefn_bridge.disconnect"
    bl_label = "Disconnect"
    bl_description = "Disconnect from UEFN"

    def execute(self, context):
        do_disconnect()
        return {"FINISHED"}


class UEFNBRIDGE_OT_send_scene(bpy.types.Operator):
    bl_idname = "uefn_bridge.send_scene"
    bl_label = "Send Full Scene"
    bl_description = "Export and send all mesh objects to UEFN"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def execute(self, context):
        if do_send_scene():
            self.report({"INFO"}, _st.status)
        else:
            self.report({"WARNING"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_send_changes(bpy.types.Operator):
    bl_idname = "uefn_bridge.send_changes"
    bl_label = "Send Changes"
    bl_description = "Send only if scene changed (geometry → full, transform → fast)"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def execute(self, context):
        do_send_changes()
        self.report({"INFO"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_send_selected(bpy.types.Operator):
    bl_idname = "uefn_bridge.send_selected"
    bl_label = "Send Selected"
    bl_description = "Export and send only selected mesh objects"

    @classmethod
    def poll(cls, context):
        return _st.connected and len(context.selected_objects) > 0

    def execute(self, context):
        if do_send_selected():
            self.report({"INFO"}, _st.status)
        else:
            self.report({"WARNING"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_bake_scene(bpy.types.Operator):
    bl_idname = "uefn_bridge.bake_scene"
    bl_label = "Bake & Send Scene"
    bl_description = "Bake textures from materials, simplify, and send"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def execute(self, context):
        if do_bake_and_send(selected_only=False):
            self.report({"INFO"}, _st.status)
        else:
            self.report({"WARNING"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_bake_selected(bpy.types.Operator):
    bl_idname = "uefn_bridge.bake_selected"
    bl_label = "Bake & Send Selected"
    bl_description = "Bake textures and send only selected objects"

    @classmethod
    def poll(cls, context):
        return _st.connected and len(context.selected_objects) > 0

    def execute(self, context):
        if do_bake_and_send(selected_only=True):
            self.report({"INFO"}, _st.status)
        else:
            self.report({"WARNING"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_start_live(bpy.types.Operator):
    bl_idname = "uefn_bridge.start_live"
    bl_label = "Start Live Sync"
    bl_description = "Auto-sync changes to UEFN"

    @classmethod
    def poll(cls, context):
        return _st.connected and not _st.live_active

    def execute(self, context):
        do_start_live()
        return {"FINISHED"}


class UEFNBRIDGE_OT_stop_live(bpy.types.Operator):
    bl_idname = "uefn_bridge.stop_live"
    bl_label = "Stop Live Sync"

    def execute(self, context):
        do_stop_live()
        return {"FINISHED"}


class UEFNBRIDGE_OT_clean(bpy.types.Operator):
    bl_idname = "uefn_bridge.clean"
    bl_label = "Clean All in UEFN"
    bl_description = "Remove all bridge-imported actors and assets from UEFN"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def invoke(self, context, event):
        # Destructive (hard-deletes BB_ actors + imported assets in UEFN) — confirm first.
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        do_clean_all()
        self.report({"INFO"}, _st.status)
        return {"FINISHED"}


class UEFNBRIDGE_OT_pull_transforms(bpy.types.Operator):
    bl_idname = "uefn_bridge.pull_transforms"
    bl_label = "Pull Transforms from UEFN"
    bl_description = "Request UEFN to push current actor transforms to Blender"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def execute(self, context):
        try:
            _send("request_push_transforms", {
                "blender_port": _st.server_port,
            })
            self.report({"INFO"}, "Requested transforms from UEFN")
        except Exception as e:
            self.report({"WARNING"}, f"Pull failed: {e}")
        return {"FINISHED"}


class UEFNBRIDGE_OT_open_url(bpy.types.Operator):
    bl_idname = "uefn_bridge.open_url"
    bl_label = "Open URL"
    url: bpy.props.StringProperty()

    def execute(self, context):
        import webbrowser
        webbrowser.open(self.url)
        return {"FINISHED"}

# ============================================================
# PROPERTIES
# ============================================================


class UEFNBridgeProperties(bpy.types.PropertyGroup):
    project_name: bpy.props.StringProperty(
        name="Project",
        default="",
        description="Optional override for the UEFN subfolder name. Leave EMPTY to use the "
                    ".blend filename automatically (recommended)",
    )
    host: bpy.props.StringProperty(
        name="Host",
        default="127.0.0.1",
        description="UEFN bridge server address",
    )
    port: bpy.props.IntProperty(
        name="Port",
        default=8790,
        min=1024,
        max=65535,
        description="UEFN bridge server port",
    )
    bake_resolution: bpy.props.IntProperty(
        name="Resolution",
        default=1024,
        min=128,
        max=4096,
        description="Bake texture resolution",
    )
    bake_metallic: bpy.props.BoolProperty(
        name="Bake Metallic",
        default=True,
        description="Include metallic channel in bake (uses emission trick)",
    )
    combine_meshes: bpy.props.BoolProperty(
        name="Combine Meshes",
        default=False,
        description="Merge the selection into ONE static mesh on import. Off (default) "
                    "keeps objects separate so each lands at its own Blender position.",
    )
    auto_apply_transforms: bpy.props.BoolProperty(
        name="Apply Rotation & Scale",
        default=True,
        description="Automatically apply rotation and scale before export to avoid transform issues",
    )

# ============================================================
# N-PANEL (collapsible sub-panels)
# ============================================================


class UEFNBRIDGE_PT_main(bpy.types.Panel):
    bl_label = "UEFN Bridge"
    bl_idname = "UEFNBRIDGE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UEFN"

    def draw_header(self, context):
        self.layout.label(
            text="",
            icon="LINKED" if _st.connected else "UNLINKED")

    def draw(self, context):
        layout = self.layout
        props = context.scene.uefn_bridge

        box = layout.box()
        row = box.row()
        if _st.connected:
            if _st.live_active:
                row.label(text="Live Sync Active", icon="REC")
            else:
                row.label(text="Connected", icon="CHECKMARK")
        else:
            row.label(text="Disconnected", icon="X")

        box.label(text=_st.status, icon="INFO")

        if _st.project_name:
            box.label(text=f"Project: {_st.project_name}", icon="FILE_FOLDER")
        if _st.project_path:
            box.label(text=f"Path: {_st.project_path}", icon="FILEBROWSER")

        mesh_count = sum(1 for o in context.scene.objects if o.type == "MESH")
        box.label(text=f"Mesh objects: {mesh_count}", icon="MESH_DATA")

        if _st.send_count > 0:
            box.label(text=f"Total syncs: {_st.send_count}", icon="RECOVER_LAST")


class UEFNBRIDGE_PT_connection(bpy.types.Panel):
    bl_label = "Connection"
    bl_idname = "UEFNBRIDGE_PT_connection"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UEFN"
    bl_parent_id = "UEFNBRIDGE_PT_main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.uefn_bridge

        if not _st.connected:
            layout.prop(props, "project_name", icon="FILE_FOLDER")
            if not props.project_name.strip():
                auto = _blend_project_name() or "Untitled — save the .blend"
                layout.label(text=f"Auto: {auto}", icon="FILE_BLEND")
            layout.separator()
            layout.prop(props, "host")
            layout.prop(props, "port")
            layout.operator("uefn_bridge.connect", icon="PLAY")
        else:
            layout.operator("uefn_bridge.disconnect", icon="CANCEL")


class UEFNBRIDGE_PT_export(bpy.types.Panel):
    bl_label = "Export & Send"
    bl_idname = "UEFNBRIDGE_PT_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UEFN"
    bl_parent_id = "UEFNBRIDGE_PT_main"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def draw(self, context):
        layout = self.layout
        props = context.scene.uefn_bridge
        layout.prop(props, "auto_apply_transforms")
        layout.prop(props, "combine_meshes")
        layout.separator()
        layout.operator("uefn_bridge.send_scene", icon="SCENE_DATA")
        layout.operator("uefn_bridge.send_changes", icon="FILE_REFRESH")
        layout.operator("uefn_bridge.send_selected", icon="RESTRICT_SELECT_OFF")
        layout.separator()
        layout.operator("uefn_bridge.clean", icon="TRASH")


class UEFNBRIDGE_PT_bake(bpy.types.Panel):
    bl_label = "Bake & Send"
    bl_idname = "UEFNBRIDGE_PT_bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UEFN"
    bl_parent_id = "UEFNBRIDGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _st.connected

    def draw(self, context):
        layout = self.layout
        props = context.scene.uefn_bridge

        layout.prop(props, "bake_resolution")
        layout.prop(props, "bake_metallic")
        layout.separator()
        layout.operator("uefn_bridge.bake_scene", icon="RENDER_STILL")
        layout.operator("uefn_bridge.bake_selected", icon="RESTRICT_SELECT_OFF")

        box = layout.box()
        box.label(text="Bake uses Cycles (CPU, 1 sample)", icon="INFO")
        box.label(text="Materials are temporarily modified")


class UEFNBRIDGE_PT_live(bpy.types.Panel):
    bl_label = "Live Sync"
    bl_idname = "UEFNBRIDGE_PT_live"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UEFN"
    bl_parent_id = "UEFNBRIDGE_PT_main"

    @classmethod
    def poll(cls, context):
        return _st.connected

    def draw(self, context):
        layout = self.layout

        if _st.live_active:
            layout.operator("uefn_bridge.stop_live", icon="PAUSE")
            layout.label(text="Every Ctrl+S syncs to UEFN", icon="REC")
        else:
            layout.operator("uefn_bridge.start_live", icon="PLAY")

        layout.separator()
        layout.operator("uefn_bridge.pull_transforms", icon="IMPORT")

        box = layout.box()
        box.label(text="Save (Ctrl+S) triggers sync", icon="FILE_TICK")
        box.label(text="Geometry change: full re-export", icon="MESH_DATA")
        box.label(text="Transform only: fast update", icon="ORIENTATION_LOCAL")
        box.label(text="Pull: get positions from UEFN", icon="IMPORT")


class UEFNBRIDGE_PT_info(bpy.types.Panel):
    bl_label = "Info"
    bl_idname = "UEFNBRIDGE_PT_info"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UEFN"
    bl_parent_id = "UEFNBRIDGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"UEFN Blender Bridge v{ADDON_VERSION}")
        layout.label(text="by KiKoZl - Surprise Co.")

        op = layout.operator("uefn_bridge.open_url", text="GitHub", icon="URL")
        op.url = "https://github.com/KiKoZl1"

        layout.separator()
        layout.label(text="")
        layout.label(text="Built for UEFN by KiKoZl")

# ============================================================
# REGISTER
# ============================================================

_classes = (
    UEFNBridgeProperties,
    UEFNBRIDGE_OT_connect,
    UEFNBRIDGE_OT_disconnect,
    UEFNBRIDGE_OT_send_scene,
    UEFNBRIDGE_OT_send_changes,
    UEFNBRIDGE_OT_send_selected,
    UEFNBRIDGE_OT_bake_scene,
    UEFNBRIDGE_OT_bake_selected,
    UEFNBRIDGE_OT_start_live,
    UEFNBRIDGE_OT_stop_live,
    UEFNBRIDGE_OT_clean,
    UEFNBRIDGE_OT_pull_transforms,
    UEFNBRIDGE_OT_open_url,
    UEFNBRIDGE_PT_main,
    UEFNBRIDGE_PT_connection,
    UEFNBRIDGE_PT_export,
    UEFNBRIDGE_PT_bake,
    UEFNBRIDGE_PT_live,
    UEFNBRIDGE_PT_info,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.uefn_bridge = bpy.props.PointerProperty(
        type=UEFNBridgeProperties)
    os.makedirs(EXCHANGE_DIR, exist_ok=True)
    _log("UEFN Bridge addon registered")


def unregister():
    if bpy.app.timers.is_registered(_process_incoming):
        bpy.app.timers.unregister(_process_incoming)
    _stop_blender_server()
    if _st.live_active:
        do_stop_live()
    if _st.connected:
        do_disconnect()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, "uefn_bridge"):
        del bpy.types.Scene.uefn_bridge
