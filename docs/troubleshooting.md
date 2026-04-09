# Troubleshooting

Common issues and how to fix them. If your problem isn't here, please
[open an issue](https://github.com/KiKoZl1/uefn-blender-bridge/issues) with:
- Blender version, UEFN version, OS
- Steps to reproduce
- Console output from both apps

---

## Connection problems

### "Connect" button does nothing / "Connection refused"

The UEFN side isn't running.

**Fix:**
1. Open UEFN
2. `Tools > Execute Python Script...`
3. Pick `uefn_blender_bridge.py`
4. Verify the Dashboard window opens and shows `Listening on port 8790`
5. Try connecting from Blender again

### "Listening on port 8791" but UEFN never registers Blender

This usually means a firewall is blocking `localhost` traffic.

**Fix:**
- Allow Blender and UnrealEditor through Windows Defender Firewall
  (`Settings > Privacy & security > Windows Security > Firewall & network protection`)
- Both apps need to talk to `127.0.0.1` on ports `8790` and `8791`

### Ports already in use

If `8790` or `8791` are taken, the bridge tries `8791..8795` and `8792..8795`
respectively. If all five fail, you'll see an error in the console.

**Fix:**
- Find what's using the port: `netstat -ano | findstr :8790`
- Kill the offending process, or restart your machine

---

## Sync problems

### Send Scene runs but nothing appears in UEFN

Check the UEFN Dashboard log for an error. Most common causes:

- **Project path not detected.** The Dashboard should show a project path.
  If it shows nothing, manually set it in the Dashboard or restart UEFN
  with the project actually open (not just on the Project Browser).
- **No selected scene to import.** The bridge writes FBX to a temp folder
  and tells UEFN where to find it. If the temp folder is gone (cleaned by
  Windows), import fails. Just click Send Scene again.

### Materials don't have textures in UEFN

Texture import sometimes fails silently if the texture file path in Blender
is broken (relative path to a deleted file).

**Fix:**
- In Blender: `File > External Data > Find Missing Files` and re-link
- Or pack textures: `File > External Data > Automatically Pack Resources`
- Then Send Scene again

### Live Sync isn't picking up Ctrl+S

Live Sync must be **explicitly enabled** — clicking Connect is not enough.

**Fix:**
1. In the UEFN sidebar, expand **Live Sync**
2. Click **Start Live Sync**
3. The status should show "Active"
4. Now `Ctrl+S` will trigger sync

### Bidirectional sync from UEFN to Blender doesn't fire on Ctrl+S

A few things to check:

- **Is the UEFN Dashboard window still open?** If you closed it, the bridge
  is gone — re-run the script.
- **Did Blender's HTTP server start?** The Blender panel should show a port
  number (8791-8795). If it shows 0, disconnect and reconnect from Blender.
- **Did anything actually change?** The UEFN side only pushes when something
  is dirty. If you saved without moving anything, nothing gets sent.
- **First-time gotcha:** the very first save after connecting may need a
  manual **Push to Blender** click from the Dashboard to seed the snapshot.

### Duplicating an actor in UEFN doesn't appear in Blender

This is a **known limitation** in v0.5.0-beta. The UEFN→Blender direction
only handles transforms of existing `BB_` actors, not new ones.

**Workaround:** create the duplicate in Blender instead, save → it'll
appear in UEFN.

---

## Coordinate / orientation problems

### Mesh appears rotated 90° or upside down in UEFN

This *should not* happen with the default export settings. The bridge uses
`bake_space_transform=True` and UEFN imports with `convert_scene=False`,
which together produce a clean Y-up to Z-up conversion.

If it's still wrong:

- **Check the mesh's transform in Blender.** If you applied a custom rotation
  in Object mode but didn't apply transforms (`Ctrl+A > Rotation & Scale`),
  the FBX exporter and importer disagree about the basis.
- **Fix:** select the object, `Ctrl+A > All Transforms`, save, re-sync.

### Scale is 100x too big or too small

Blender's unit system might be set to something other than meters.

**Fix:**
- `Properties > Scene > Units > Unit System: Metric`
- `Length: Meters`
- `Unit Scale: 1.0`

---

## Material / texture problems

### "Material Instance not found" in UEFN log

The bridge creates a master material the first time it imports textures.
If something interrupted that step, the MI references a missing parent.

**Fix:**
- In UEFN: delete the broken MI under
  `Content/{Project}/BlenderBridge/{Name}/Materials/`
- Run **Send Scene** again from Blender

### Texture-only sync doesn't update the texture in UEFN

The bridge tries to rebind the existing Material Instance. If it can't find
the MI, it falls back to re-importing materials.

**Fix:**
- Try Send Scene (full sync) once to recreate everything
- Then texture-only sync should work on subsequent saves

---

## Performance problems

### Live Sync is slow / Blender lags during save

This usually means the diff is detecting too many "changed" objects.

**Fix:**
- If you imported a heavy mesh recently, the first sync after that takes
  longer — that's expected.
- If every save is slow even when nothing changed, the per-object hashing
  may be hitting an edge case. Open an issue with your scene size.

### UEFN freezes when importing large FBX

Large FBX imports are blocking — UEFN's import pipeline is single-threaded.
The Dashboard will look frozen until the import finishes.

**Fix:** wait it out. For very large scenes (500+ objects), expect 10-30
seconds of UEFN being unresponsive.

---

## Last resort

### Full reset

If everything is broken:

1. Close UEFN and Blender
2. Delete temp folder: `%TEMP%\UEFNBlenderBridge\`
3. In UEFN: delete `Content/{Project}/BlenderBridge/{ProjectName}/` (the
   whole folder for your bridge project)
4. Reopen both apps, reconnect, Send Scene fresh

This is destructive — you'll lose any custom edits you made on the UEFN
side under `BlenderBridge/`. Make sure that's OK before doing it.

---

## Still stuck?

Open an issue at [github.com/KiKoZl1/uefn-blender-bridge/issues](https://github.com/KiKoZl1/uefn-blender-bridge/issues).

Include:
- The error message (exact text)
- What you were doing when it happened
- Blender System Console output (`Window > Toggle System Console`)
- UEFN Output Log (`Window > Developer Tools > Output Log`, set Categories
  to All)
- A minimal `.blend` file if you can share one
