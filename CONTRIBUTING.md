# Contributing

Thanks for your interest in improving the **UEFN ↔ Blender Bridge**!

## Project shape

Two halves, same machine, talking over local HTTP on `127.0.0.1:8790–8795`:

- `BlenderAddon/uefn_bridge/` — runs **inside Blender** (addon: panels, operators, FBX export, live sync).
- `UEFNPlugin/uefn_blender_bridge.py` — runs **inside the UEFN editor** (imports `unreal`, tkinter dashboard).

Because `unreal.*` (and `bpy`) are not thread-safe, neither side touches them from the HTTP/socket
thread — commands are queued and applied on the main thread (UEFN: Slate post-tick callback;
Blender: `bpy.app.timers`).

## Dev setup

1. Edit the files in this repo (source of truth).
2. **Blender:** install `BlenderAddon/uefn_bridge/` as an addon (zip the folder, or symlink it into your
   addons folder), then enable it in `Preferences ▸ Add-ons`.
3. **UEFN:** `Tools ▸ Execute Python Script ▸ uefn_blender_bridge.py`. Run only **one** instance.

## Conventions

- Python 3.10+ (Blender) / 3.11 (UEFN embedded). Standard library only on both sides (plus the host
  apps' bundled `bpy`, `unreal`, tkinter). **No third-party pip deps.**
- Keep it dependency-light and same-machine simple.
- **Never** touch `bpy` or `unreal.*` off the main thread (the cardinal rule).
- Crash loud, never corrupt silently — surface errors to the panel/log instead of `except: pass`.
- Verify a change compiles:
  `python -m py_compile BlenderAddon/uefn_bridge/bridge.py UEFNPlugin/uefn_blender_bridge.py`
- Match the existing terse style.

## Pull requests

- Describe what you changed and how you tested it (see the PR template).
- One focused change per PR where possible.

## Reporting bugs

Open an issue with your Blender version, UEFN/Fortnite version, OS, steps to reproduce, and the logs
from the Blender system console + the UEFN dashboard activity panel.
