# Quickstart

Your first round-trip in 5 minutes. Assumes you've already done [Installation](installation.md).

---

## Step 1 — Open both apps

1. Open **UEFN** with the project you want to push assets to.
2. Open **Blender** with a fresh scene (or your own scene with some meshes).

---

## Step 2 — Start the bridge in UEFN

1. In UEFN, go to `Tools > Execute Python Script...`
2. Pick `uefn_blender_bridge.py`
3. The **UEFN Bridge Dashboard** window opens. You should see:
   - **Status:** `Listening on port 8790`
   - **Bridge Project:** *(empty)*

Leave that window open. Closing it stops the bridge.

---

## Step 3 — Connect from Blender

1. In Blender's 3D viewport, press `N` to open the sidebar.
2. Click the **UEFN** tab.
3. In the **Connection** panel:
   - Enter a **Project Name** — for example `Demo01`
   - Click **Connect**
4. The panel switches to "Connected" and shows the bridge port (8791).
5. Check the UEFN Dashboard — it should now display:
   - **Bridge Project:** `Demo01`
   - A log line: `Blender connected: v4.2.0, project: Demo01`

> The project name decides where assets are stored on the UEFN side:
> `/Game/YourUEFNProject/BlenderBridge/Demo01/Meshes|Materials|Textures/`

---

## Step 4 — Send your first scene

1. In Blender, make sure you have at least one mesh object in the scene.
   (A default cube works fine.)
2. In the UEFN sidebar, expand **Environment Tools**.
3. Click **Send Scene**.
4. Watch the UEFN Dashboard log — you should see lines like:
   - `Importing FBX: Scene.fbx`
   - `Imported 1 mesh(es)`
   - `Spawned 1 actor(s)`
5. Switch to UEFN. Your cube should be in the level as `BB_Cube`, sitting
   in `World Outliner > BlenderBridge`.
6. Browse to `Content/{YourUEFNProject}/BlenderBridge/Demo01/Meshes/` — your
   imported `SM_Cube` (or similar) is there.

---

## Step 5 — Enable Live Sync

Now the fun part — automatic two-way sync.

1. In Blender, in the UEFN sidebar, expand **Live Sync**.
2. Click **Start Live Sync**.
3. The button changes to **Stop Live Sync** and the status shows "Active".

From this point on:

- **Move/edit/add an object in Blender** → press `Ctrl+S` → it syncs to UEFN
  automatically. Only the things that changed get sent.
- **Move a `BB_` actor in UEFN** → press `Ctrl+S` in UEFN → the new transform
  reflects back in Blender.

---

## Step 6 — Try it

A few things to test:

1. **Move the cube in Blender**, save (`Ctrl+S`). Watch UEFN — the actor moves.
2. **Move the cube in UEFN**, save the level (`Ctrl+S`). Switch to Blender —
   the object moved.
3. **Add a new mesh in Blender** (Shift+A → Mesh → UV Sphere), save. UEFN
   should spawn a new `BB_Sphere`.
4. **Change a material color in Blender** (Shader Editor → Principled BSDF
   → Base Color), save. The UEFN Material Instance updates without
   re-exporting the FBX.
5. **Drag the cube into a Blender Collection** named "Buildings", save. In
   UEFN, the actor moves to `World Outliner > BlenderBridge > Buildings`.

---

## What you just did

You set up a **save-driven, bidirectional, diff-based** asset pipeline
between Blender and UEFN. The bridge handles:

- FBX export/import (with correct axis baking, no manual rotation fixing)
- PBR material reconstruction in UEFN with auto-channel detection
- Texture-only updates without re-exporting geometry
- Hierarchy preservation (Collections → World Outliner folders)
- Per-object hashing to send only what changed
- Save-triggered round-trip — no polling, no wasted CPU

---

## Common next steps

- Read [troubleshooting.md](troubleshooting.md) if anything went sideways
- Read the [README](../README.md) for the full feature list and current
  limitations
- Report bugs at [github.com/KiKoZl1/uefn-blender-bridge/issues](https://github.com/KiKoZl1/uefn-blender-bridge/issues)

Have fun. Build cool stuff. Don't ship anything important without backups.
