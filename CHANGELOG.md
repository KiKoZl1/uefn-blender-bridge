# Changelog

All notable changes to **UEFN Blender Bridge** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.5.0-beta]: https://github.com/KiKoZl1/uefn-blender-bridge/releases/tag/v0.5.0-beta
