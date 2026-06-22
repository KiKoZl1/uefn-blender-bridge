# UEFN ↔ Blender Bridge

> The only Blender ↔ UEFN bridge with real two-way live sync. Build in Blender, see it in UEFN — and back again — on every save.

**Status:** `v1.0.0` — stable. See [CHANGELOG.md](CHANGELOG.md).
**License:** [MIT](LICENSE)
**Made by:** [KiKoZl](https://github.com/KiKoZl1) · [Surprise Co.](https://surpriseugc.com)

---

## What it is

**UEFN ↔ Blender Bridge** is the only bridge between Blender and UEFN with
genuine **two-way live sync**. Model in Blender and
your meshes, materials, and textures land in UEFN — correctly placed, correctly
named, correctly scaled. Move an actor in UEFN and the change flows straight
back to Blender. No FBX juggling, no folder hunting, no manual axis fixing.

It runs as a Blender add-on talking to a UEFN Python plugin over `localhost`
HTTP. Heavy data (geometry, textures) moves via a temporary FBX/PNG handoff;
lightweight metadata travels over the socket. Both apps run on the same
machine.

---

## Features

- **Two-way live sync.** `Ctrl+S` in Blender pushes changes to UEFN; `Ctrl+S`
  in UEFN pushes actor edits back to Blender. The send is async — the Blender
  UI never freezes.
- **Data-driven transforms.** The mesh is exported pure (no baked transform);
  the UEFN actor carries the full world location, rotation, and scale. Correct
  for any object — parented, unparented, or scaled.
- **Collections become folders.** Blender collections map to subfolders in
  both the UEFN Content Browser and the World Outliner. The project folder is
  named automatically after the `.blend` filename — nothing to type.
- **Instancing.** Objects sharing one Blender mesh datablock import a single
  StaticMesh and spawn N actors — fewer draw calls.
- **LOD.** `<base>_LOD<N>` groups import as one StaticMesh with LOD levels and
  a single actor, including a forest of LOD'd instances (N actors, one LOD'd
  mesh).
- **UE5 materials.** One shared master `MM_BlenderBridge` plus per-material
  `MI_` instances, deduped — a material shared by N meshes becomes a single
  instance — routed to `Materials/<collection>/`. Handles both flat-color and
  textured (`T_<material>_<channel>`) materials.
- **Clean asset naming.** `SM_` (StaticMesh), `MM_`/`MI_` (master/instance
  material), `T_` (texture). The source name is always preserved.
- **Auto-collision and Nanite** applied on import.
- **Stable identity.** A per-object GUID survives rename and duplicate.
- **Scoped safety.** "Clean All" affects only the **current** project and asks
  for confirmation; destructive operations are gated.
- **Quality of life.** Async non-blocking sends and in-panel error surfacing.

---

## Requirements

| | Minimum | Tested |
|---|---|---|
| **Blender** | 3.6 LTS | 3.6, 4.0, 4.2 |
| **UEFN** | 40.00+ (Python era) | 41.10 |
| **OS** | Windows 10 | Windows 11 |
| **Python** | (bundled) | Blender 3.10+, UEFN 3.11 |

> Both apps must run on the same machine — they talk over `localhost`. macOS
> and Linux are not officially tested; the code is platform-agnostic Python, so
> it *should* work, but you're on your own there for now.

---

## Install

Get the latest [GitHub release](https://github.com/KiKoZl1/uefn-blender-bridge/releases/latest)
or clone this repo. Installation has two parts.

### 1. The Blender add-on

1. Copy the `BlenderAddon/uefn_bridge/` folder into your Blender add-ons
   directory:

   | OS | Path |
   |---|---|
   | **Windows** | `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\` |
   | **macOS** | `~/Library/Application Support/Blender/<version>/scripts/addons/` |
   | **Linux** | `~/.config/blender/<version>/scripts/addons/` |

2. In Blender, open `Edit > Preferences > Add-ons`, search for
   **UEFN Blender Bridge**, and tick the checkbox.
3. Press `N` in the 3D viewport to reveal the sidebar — a **UEFN** tab appears.

### 2. The UEFN Python plugin

1. Save `UEFNPlugin/uefn_blender_bridge.py` somewhere stable (e.g.
   `Documents/UEFN/uefn_blender_bridge.py`).
2. Open your UEFN project.
3. Run it via `Tools > Execute Python Script...` and pick the file. The Bridge
   Dashboard window opens.

> Make sure the **Python Editor Script Plugin** is enabled in `Edit > Plugins`.
> To auto-run the script on startup, see [docs/installation.md](docs/installation.md).

---

## Quickstart

1. **Connect.** In UEFN, run `uefn_blender_bridge.py` — the Dashboard shows
   `Listening on port 8790`. In Blender, open the **UEFN** sidebar (`N`) and
   click **Connect**. The Dashboard logs the connection and shows your project.
2. **Send.** Click **Send Full Scene** in Blender. Your meshes import to UEFN, get
   spawned as actors, and land under
   `BlenderBridge/<project>/Meshes/<collection>/`.
3. **Live sync.** Click **Start Live Sync**. From now on, `Ctrl+S` in either
   app pushes changes both ways — move the cube in Blender and the UEFN actor
   moves; move the actor in UEFN and the Blender object follows.

Full walkthrough in [docs/quickstart.md](docs/quickstart.md).

---

## Content structure

The bridge organizes everything under a single project root, named
automatically after your `.blend` filename. Blender collections become
subfolders inside each asset type:

```
BlenderBridge/<project>/
├── Meshes/
│   └── <collection>/
│       └── SM_<name>
├── Materials/
│   └── <collection>/
│       ├── MM_BlenderBridge   (shared master)
│       └── MI_<material>      (deduped instances)
└── Textures/
    └── <collection>/
        └── T_<material>_<channel>
```

The same collection hierarchy is mirrored in the UEFN World Outliner, so the
Content Browser and the level stay in lockstep.

---

## Documentation

- [docs/installation.md](docs/installation.md) — full Blender + UEFN setup
- [docs/quickstart.md](docs/quickstart.md) — your first round-trip in 5 minutes
- [docs/troubleshooting.md](docs/troubleshooting.md) — common issues and fixes
- [CHANGELOG.md](CHANGELOG.md) — what changed across versions

---

## License

Released under the [MIT License](LICENSE).

---

by KiKoZl - Surprise Co.
