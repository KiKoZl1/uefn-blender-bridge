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

### "Listening on port 8790" but UEFN never registers Blender

This usually means a firewall is blocking `localhost` traffic.

**Fix:**
- Allow Blender and UnrealEditor through Windows Defender Firewall
  (`Settings > Privacy & security > Windows Security > Firewall & network protection`)
- Both apps need to talk to `127.0.0.1` on ports `8790` and `8791`

### Ports already in use

If the default ports are taken, the bridge scans a small range: the UEFN side
tries `8790..8795` and the Blender side tries `8791..8795`. If none are free,
you'll see an error in the console.

**Fix:**
- Find what's using the port: `netstat -ano | findstr :8790`
- Kill the offending process, or restart your machine

---

## Sync problems

### Send Full Scene runs but nothing appears in UEFN

Check the UEFN Dashboard log for an error. Most common causes:

- **Project path not detected.** The Dashboard should show a project path.
  If it shows nothing, manually set it in the Dashboard or restart UEFN
  with the project actually open (not just on the Project Browser).
- **No selected scene to import.** The bridge writes FBX to a temp folder
  and tells UEFN where to find it. If the temp folder is gone (cleaned by
  Windows), import fails. Just click Send Full Scene again.

### Materials don't have textures in UEFN

Texture import sometimes fails silently if the texture file path in Blender
is broken (relative path to a deleted file).

**Fix:**
- In Blender: `File > External Data > Find Missing Files` and re-link
- Or pack textures: `File > External Data > Automatically Pack Resources`
- Then Send Full Scene again

### Live Sync isn't picking up Ctrl+S

Live Sync must be **explicitly enabled** — clicking Connect is not enough.

**Fix:**
1. In the UEFN sidebar, expand **Live Sync**
2. Click **Start Live Sync**
3. The status should show "Active"
4. Now `Ctrl+S` will trigger sync

### Edits in UEFN don't reflect back in Blender on Ctrl+S

Two-way sync is live: editing a `BB_` actor in UEFN and pressing `Ctrl+S`
pushes the change back to Blender. If it isn't firing:

- **Is the UEFN Dashboard window still open?** If you closed it, the bridge
  is gone — re-run the script.
- **Did Blender's HTTP server start?** The Blender panel should show a port
  number (8791-8795). If it shows 0, disconnect and reconnect from Blender.
- **Did anything actually change?** The UEFN side only pushes when something
  is dirty. If you saved without moving anything, nothing gets sent.

---

## Coordinate / orientation problems

Rotation and scale are handled by a **data-driven transform**: the mesh is
exported pure (no baked transform) and the UEFN actor carries the full world
location, rotation, and scale. This is correct for any object — parented,
unparented, or scaled — so the old 90°-rotation and "100x too big/too small"
problems no longer occur.

### A mesh still looks wrong in UEFN

If a single object lands in an unexpected place or orientation, the most
likely cause is the object's own transform in Blender, not the bridge.

- **Check the object's transform in Blender.** Confirm its location,
  rotation, and scale are what you expect in the N-panel `Item` tab.
- **Re-sync.** Move or touch the object, save (`Ctrl+S`), and the corrected
  world transform is pushed to the UEFN actor.

---

## Material / texture problems

### "Material Instance not found" in UEFN log

The bridge creates a master material the first time it imports textures.
If something interrupted that step, the MI references a missing parent.

**Fix:**
- In UEFN: delete the broken MI under
  `Content/{Project}/BlenderBridge/{Name}/Materials/`
- Run **Send Full Scene** again from Blender

### Texture-only sync doesn't update the texture in UEFN

The bridge tries to rebind the existing Material Instance. If it can't find
the MI, it falls back to re-importing materials.

**Fix:**
- Try Send Full Scene once to recreate everything
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
4. Reopen both apps, reconnect, Send Full Scene fresh

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
