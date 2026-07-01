# Changelog

All notable changes to **UEFN Blender Bridge** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.2] — 2026-06-23

Launch-hardening patch. A pre-launch audit surfaced two data-loss bugs on the
primary workflows plus two docs claims that overstated the code — all fixed.

### Fixed

- **Texture export no longer rewrites your image paths.** Sending textures used
  to repoint your Blender image datablocks at a temporary folder (wiped on the
  next send), which could break the textures in your own `.blend` after a save.
  The export now writes to the temp folder without mutating your datablocks.
- **Clean no longer deletes your own `BB_`-named actors.** The orphan sweep now
  only removes actors that carry a bridge tag, so a light or trigger you happened
  to name `BB_*` is never touched.
- **Asset names are fully UEFN-safe.** The sanitizer now whitelists
  `A-Z a-z 0-9 _` (no `-`, no leading digit, ASCII only), matching UEFN's
  stricter validator — names like `Roof-tiles` no longer fail import.
- **UEFN dashboard footer** now renders with the correct branding.

### Added

- **Nanite** is now actually enabled on imported StaticMeshes.

### Changed

- The **Bake & Send** panel is temporarily hidden while its material pipeline is
  finished (the operators remain, just not surfaced in the UI).
- Docs corrected: port-retry ranges, version labels, and the scope of the
  async-send claim.

---

## [1.0.1] — 2026-06-23

Patch release. The first real, multi-object textured scene test surfaced four
issues — all fixed and live-validated: a much faster import, no more timeout, a
correct Clean, and meshes whose names contain spaces now import.

### Fixed

- **Clean now reliably removes the project's actors.** The previous scope relied
  on an Outliner-folder lookup that isn't available in current UEFN builds, so
  Clean deleted the assets but left the actors behind, mesh-less, in the
  Outliner. Actors are now scoped by a per-project tag, with an orphan sweep
  that clears any mesh-less bridge actor left by an earlier clean.
- **No more "Gateway Timeout" on large imports.** The UEFN-side request deadline
  was too short for a heavy textured scene; a long single-threaded import no
  longer times out mid-flight.
- **Meshes with spaces in their name** (e.g. `Human Architecture Building`) were
  silently skipped — neither placed nor given materials. Object names are now
  matched the same way UEFN sanitizes asset names.

### Changed

- **Much faster import** (roughly 4× on a textured scene). The FBX no longer
  carries or imports embedded materials/textures — the `MM_`/`MI_` pipeline is
  the single source of materials. One shared textured master is compiled per
  channel-set (`MM_Tex_BC_R`, …) instead of one per material, and textures are
  saved once instead of twice.

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

[1.0.2]: https://github.com/KiKoZl1/uefn-blender-bridge/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/KiKoZl1/uefn-blender-bridge/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/KiKoZl1/uefn-blender-bridge/compare/v0.5.0-beta...v1.0.0
[0.5.0-beta]: https://github.com/KiKoZl1/uefn-blender-bridge/releases/tag/v0.5.0-beta
