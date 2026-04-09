# UEFN Blender Bridge

> Live two-way bridge between Blender and Unreal Editor for Fortnite (UEFN).
> Build environments in Blender, see them in UEFN. Move actors in UEFN, see
> them back in Blender. No FBX juggling, no folder hunting.

**Status:** `v0.5.0-beta` — early access. Expect bugs. See [CHANGELOG.md](CHANGELOG.md).
**License:** [Source Available — free for personal & commercial UEFN use, no redistribution](LICENSE)
**Made by:** [KiKoZl](https://github.com/KiKoZl1) · [Surprise Co.](https://surpriseugc.com)

---

## What it does

- **Push from Blender to UEFN.** Save in Blender (`Ctrl+S`) and your meshes,
  materials, textures, and transforms appear in UEFN — automatically organized
  into `BlenderBridge/{ProjectName}/Meshes|Materials|Textures/`.
- **Push from UEFN to Blender.** Move a `BB_` actor in UEFN, save the level
  (`Ctrl+S`), and the new transform shows up in Blender. No polling.
- **Smart diff sync.** Only what changed gets sent — geometry, transforms,
  materials, and textures are tracked independently.
- **Texture-only updates.** Swap a texture in Blender without re-exporting
  the whole mesh.
- **Hierarchy sync.** Blender Collections become UEFN World Outliner folders.
- **Organized projects.** Set a project name once; everything stays scoped
  under `BlenderBridge/{YourProjectName}/` so multiple users can share a UEFN
  project without stepping on each other.

## What it doesn't do (yet)

- Animation, rigging, or skeletal meshes
- Lights, cameras, or non-mesh actors
- Duplicating an actor in UEFN does **not** create a new object in Blender
  (the reverse direction works fine)
- Niagara, Blueprints, or anything Verse-specific

This is **environment tooling**, not a full DCC bridge. Scope creep is the
enemy.

---

## Quick install

### Option A — Download the release (recommended)

1. Download the latest [`uefn-blender-bridge-v0.5.0-beta.zip`](https://github.com/KiKoZl1/uefn-blender-bridge/releases/latest)
2. Extract anywhere
3. Follow the `README.txt` inside

### Option B — Clone this repo

```bash
git clone https://github.com/KiKoZl1/uefn-blender-bridge.git
```

Then copy:
- `BlenderAddon/uefn_bridge/` → your Blender addons folder
- `UEFNPlugin/uefn_blender_bridge.py` → run via UEFN's Python console

Full step-by-step in [docs/installation.md](docs/installation.md).

---

## Quick start

1. **In Blender:** Open the UEFN sidebar (`N` key → UEFN tab), type a
   project name, click **Connect**.
2. **In UEFN:** Run `uefn_blender_bridge.py` from `Tools > Execute Python
   Script`. The Dashboard window opens.
3. **In Blender:** Click **Send Scene**. Your meshes appear in UEFN under
   `Content/YourProject/BlenderBridge/{ProjectName}/Meshes/`.
4. **Enable Live Sync** in Blender. Now `Ctrl+S` in either app pushes
   changes both ways.

Full walkthrough in [docs/quickstart.md](docs/quickstart.md).

---

## How it works

```
┌─────────────────┐                       ┌─────────────────┐
│     Blender     │  HTTP :8790 (client)  │      UEFN       │
│                 │ ────────────────────► │                 │
│  bridge.py      │                       │ uefn_blender_   │
│                 │ ◄──────────────────── │ bridge.py       │
│                 │  HTTP :8791 (server)  │                 │
└─────────────────┘                       └─────────────────┘
```

- **FBX pipeline** with `bake_space_transform=True` on export and
  `convert_scene=False` on import — no axis flips, no Z-up confusion.
- **Coordinate mapping:** `UE.X = BL.X*100`, `UE.Y = -BL.Y*100`,
  `UE.Z = BL.Z*100`.
- **Per-object hashing** lets the addon know exactly what changed since
  the last sync, so re-exports stay surgical.
- **Save-triggered sync** on both sides — no polling loops, no wasted CPU.

---

## Documentation

- [Installation](docs/installation.md) — step-by-step Blender + UEFN setup
- [Quickstart](docs/quickstart.md) — your first sync in 5 minutes
- [Troubleshooting](docs/troubleshooting.md) — common issues and fixes
- [Changelog](CHANGELOG.md) — what changed across versions

---

## Bug reports & feedback

- **Bugs / crashes:** [open an issue](https://github.com/KiKoZl1/uefn-blender-bridge/issues)
- **Feature requests:** [start a discussion](https://github.com/KiKoZl1/uefn-blender-bridge/discussions)
- **General feedback:** [surpriseugc.com](https://surpriseugc.com)

When reporting bugs, please include:
- Blender version, UEFN version, OS
- Steps to reproduce
- Console output from both apps (Blender System Console + UEFN Output Log)

---

## Acknowledgments

This bridge stands on top of years of community work — `bpy.ops.export_scene.fbx`,
`unreal.AssetImportTask`, `unreal.MaterialEditingLibrary`, and the patient
trial-and-error of every TA who fought with FBX axes before us. Thank you.

---

**Reminder:** This is beta software. Always back up your `.uefnproject` and
`.blend` files before testing. Loss of work is possible.
