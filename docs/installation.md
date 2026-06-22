# Installation

Two pieces need to be installed: a **Blender addon** and a **UEFN Python script**.
They talk to each other over `localhost` HTTP, so both apps must run on the
same machine.

---

## Requirements

| | Minimum | Tested |
|---|---|---|
| **Blender** | 3.6 LTS | 3.6, 4.0, 4.2 |
| **UEFN** | 40.00+ (Python era) | 41.10 (latest) |
| **OS** | Windows 10 | Windows 11 |
| **Python** | (bundled) | Blender 3.10+, UEFN 3.11 |

> macOS and Linux are not officially tested. The code is platform-agnostic
> Python, so it *should* work, but you're on your own for now.

---

## 1. Install the Blender addon

### Find your Blender addons folder

Blender stores user addons here:

| OS | Path |
|---|---|
| **Windows** | `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\` |
| **macOS** | `~/Library/Application Support/Blender/<version>/scripts/addons/` |
| **Linux** | `~/.config/blender/<version>/scripts/addons/` |

Replace `<version>` with your Blender version (e.g. `4.2`).

> Tip: in Blender, go to `Edit > Preferences > File Paths > Data > Scripts`
> to see (or override) where addons are loaded from.

### Copy the addon

Copy the entire `BlenderAddon/uefn_bridge/` folder into the addons directory.
You should end up with:

```
.../scripts/addons/
└── uefn_bridge/
    ├── __init__.py
    └── bridge.py
```

### Enable it

1. Open Blender
2. `Edit > Preferences > Add-ons`
3. Search for **UEFN Blender Bridge**
4. Tick the checkbox to enable
5. Close Preferences

You should now see a **UEFN** tab in the 3D Viewport sidebar (press `N` to
toggle the sidebar).

---

## 2. Install the UEFN script

UEFN doesn't have a traditional editor "plugin" system — Python scripts are
run on demand. There are two ways to run the bridge script.

### Option A — Run from the Tools menu (simple)

1. Save `UEFNPlugin/uefn_blender_bridge.py` somewhere stable on your machine
   (e.g. `Documents/UEFN/uefn_blender_bridge.py`).
2. Open your UEFN project.
3. `Tools > Execute Python Script...`
4. Pick `uefn_blender_bridge.py`
5. The Dashboard window opens.

You'll need to run this every time you reopen UEFN.

### Option B — Auto-run on startup (advanced)

If you want the bridge to start automatically when UEFN opens:

1. Locate your UEFN project's `Config/DefaultEngine.ini` (or create
   `Config/DefaultEditor.ini` if it doesn't exist).
2. Add these lines:

```ini
[/Script/PythonScriptPlugin.PythonScriptPluginSettings]
+StartupScripts=C:/Path/To/uefn_blender_bridge.py
```

3. Restart UEFN. The Dashboard should appear automatically.

> ⚠️ Use forward slashes `/` in the path even on Windows.

### Verify the Python plugin is enabled

If running the script does nothing:

1. `Edit > Plugins`
2. Search for **Python Editor Script Plugin**
3. Make sure it's enabled
4. Restart UEFN if you had to enable it

---

## 3. Verify the connection

1. **In UEFN:** the Dashboard should show:
   - Status: `Listening on port 8790`
   - Bridge Project: *(empty until Blender connects)*
2. **In Blender:** save your `.blend` first (`Ctrl+S`) — the filename becomes
   the project name automatically. There is no project field to type.
3. Open the UEFN sidebar (`N` key), go to the **Connection** panel. It shows
   `Project: <your .blend filename>`. Click **Connect**.
4. The Blender panel switches to **Connected** and shows the bridge port (8791).
5. The UEFN Dashboard should now show your project name and a log line
   like `Blender connected: v4.2.0, project: <your .blend filename>`.

You're done. Head to [Quickstart](quickstart.md) for your first sync.

---

## Updating

To update the addon:

1. Disable it in Blender Preferences
2. Replace the `uefn_bridge/` folder with the new version
3. Re-enable the addon

To update the UEFN script: just replace `uefn_blender_bridge.py` and re-run it.

---

## Uninstalling

**Blender:**
1. `Edit > Preferences > Add-ons`
2. Find **UEFN Blender Bridge**
3. Click the dropdown arrow → **Remove**

**UEFN:** delete `uefn_blender_bridge.py`. If you added it to startup, remove
the `+StartupScripts=` line from `DefaultEngine.ini`.

The bridge does not write to system folders or registry. Removing the files
is enough.
