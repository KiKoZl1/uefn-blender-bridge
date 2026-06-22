# Changelog

All notable changes to **UEFN Blender Bridge** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-06-22

First stable release. The leap from `0.5.0-beta`: two-way live sync is now
reliable in **both** directions, transforms are correct in every case, and the
content pipeline produces clean, deduplicated UE5 assets organized to match your
Blender scene. All known limitations from the beta (UEFN → Blender sync,
gigantic scale, broken rotation) are resolved.

### Added

- **Mesh instancing** — objects sharing one Blender mesh datablock import a
  single `StaticMesh` and spawn N actors, cutting draw calls
- **LOD groups** — `<base>_LOD<N>` objects import as one `StaticMesh` with proper
  LOD levels and a single actor; supports a forest of LOD'd instances (N actors,
  one LOD'd mesh)
- **MM_/MI_ material pipeline** — one shared master material `MM_BlenderBridge`
  plus per-material `MI_` instances, deduplicated (a material shared by N meshes
  becomes one instance); handles both flat-color and textured
  (`T_<material>_<channel>`) materials, routed to `Materials/<collection>/`
- **Asset naming convention** — `SM_` (StaticMesh), `MM_`/`MI_` (master/instance
  material), `T_` (texture); the source name is always preserved
- **Auto-collision and Nanite** applied on import
- **Outliner subfolders** — Blender collections now become subfolders in the
  World Outliner as well as the Content Browser
- **Stable per-object GUID identity** that survives rename and duplicate
- **In-panel error surfacing** — failures report directly in the Blender panel
- **Async non-blocking send** — exports run off the main thread so the Blender
  UI never freezes

### Changed

- **Project folder is now automatic** — named after the `.blend` filename; there
  is no project field to type
- **Data-driven transform** — meshes export pure (no baked transform) and the
  UEFN actor carries the full world location/rotation/scale, so placement is
  correct for any object, parented or unparented, scaled or not
- **Content structure** — Blender collections map to subfolders in both the UEFN
  Content Browser and the World Outliner

### Fixed

- **Two-way live sync now reliable in both directions** — Blender `Ctrl+S`
  pushes changes to UEFN and UEFN `Ctrl+S` pushes actor edits back to Blender
- **Gigantic scale on import** — meshes now arrive at correct real-world scale
- **Broken rotation** — actor orientation is now correct in all cases
- **UEFN → Blender sync** — the reverse direction is now dependable
- **Clean All scoped to the current project** — destructive cleanup no longer
  touches other projects and confirms before running

---

## [0.5.0-beta] — 2026-04-08

First public early-access release.

### Added

- **Blender → UEFN pipeline**
  - FBX export with `bake_space_transform=True` and per-object isolation
  - Smart diff sync with per-object hashing across 5 categories: transform,
    geometry, material, texture, and add/remove
  - PBR material creation in UEFN with auto-channel detection (BaseColor,
    Normal, Roughness, Metallic, Emissive, AO, Opacity, Specular, Height)
  - Texture-only updates without re-exporting mesh geometry
  - Hierarchy sync — Blender Collections become UEFN World Outliner folders
- **UEFN → Blender bidirectional sync**
  - Blender HTTP server (port 8791) receives transform updates from UEFN
  - Save-triggered push from UEFN (Ctrl+S in the editor)
  - Diff-based push — only changed `BB_` actor transforms get sent
  - Loop prevention via `receiving` flag and snapshot reset
- **Project organization system**
  - Project name required before connecting
  - All assets scoped under `BlenderBridge/{ProjectName}/Meshes|Materials|Textures/`
  - Multiple users can share a UEFN project without collisions
- **Environment Tools category** in the Blender sidebar UI
- **Live Sync mode** with manual `Send Changes`, `Pull Transforms`, and
  Dashboard `Push to Blender` buttons
- **UEFN Dashboard** with status, log ring, scene cards, and bridge project
  display
- **HTTP bridge** with auto-port discovery (8790–8795)

### Known issues

- Duplicating a `BB_` actor in UEFN does NOT create a corresponding object in
  Blender (the reverse direction works fine)
- Animation, rigging, lights, and cameras are not supported
- First sync after opening a level may show "no project" until you connect
  from Blender
- Tkinter Dashboard may flicker on some Windows display configurations
- Very large scenes (500+ objects) have not been stress-tested

### Notes

- Tested on: Blender 3.6 / 4.x, UEFN 41.10 (latest), Windows 11
- Not tested on: macOS, Linux

---

[1.0.0]: https://github.com/KiKoZl1/uefn-blender-bridge/compare/v0.5.0-beta...v1.0.0
[0.5.0-beta]: https://github.com/KiKoZl1/uefn-blender-bridge/releases/tag/v0.5.0-beta
