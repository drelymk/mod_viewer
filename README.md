# 3DMigoto Mod Viewer

Preview 3DMigoto character mods in 3D **without launching the game**.

Point it at a mod folder and it reads the `.ini` files and buffer data directly,
rebuilds the meshes, and renders them in an interactive 3D view. No game, no
load screen, no character select — just open the folder and look at the model.

Supports mods for: ZZMI, GIMI, WWMI, SRMI

![3dmigoto-mod-viewer](https://github.com/drelymk/mod_viewer/blob/main/media/3DMigoto%20Mod%20Viewer.jpg)

## Showing and hiding parts

There are two ways to control what you see:

- **Toggle panel (right)** — mirrors the in-game hotkeys. Every cycle-type
  `[Key...]` section in the mod becomes a button showing its key binding and
  current value. Click it to cycle, exactly as pressing that key would in game,
  and the affected meshes appear or disappear. Handy for checking that all the
  outfit variants and swaps actually work before you load the game.
- **Meshes panel (left)** — a checkbox per mesh, grouped by component and
  collapsible. Click any mesh to force it visible or hidden, regardless of what
  the toggles say. Useful for inspecting a single part, or for seeing pieces
  that are hidden in the mod's default state.

Both stay in sync: cycling a toggle updates the mesh checkboxes to match, while
a manual click always wins for that mesh.

Camera: drag to orbit, scroll to zoom, right-drag to pan. **Wireframe**,
**Grid** and **Shading** toggles are in the toolbar.

## Adding and editing toggles

The Toggle panel isn't just for viewing — it can author new key bindings too:

- **＋** (panel header) adds a new toggle: pick the ini file (if the mod has
  more than one), give it a name, key binding, variable name, and its list of
  cycle values.
- **✎** on a toggle edits its name, key binding, or values.
- **🗑** on a toggle deletes it, cleaning up its variable and any `if`/`elif`
  gates that referenced it.

A newly added toggle doesn't show or hide anything by itself — it needs to be
**wired** to meshes first, which is what Record mode is for. An unwired toggle
shows a **⚠** warning and blocks Export until it's wired or deleted.

## Record mode

Click **⏺** on a toggle to record what each of its values should show. The
Meshes panel becomes the recording surface: check/uncheck meshes for the
current value, click through each position, then **Save** — the app rewrites
the mod's `if`/`elif` conditions to match automatically, no manual ini editing
required. **Cancel** leaves everything as it was.

While recording, opening a different mod and the rest of the Toggle panel are
locked so nothing else changes underneath the session.

## Exporting changes

Every add, edit, delete, and recording is staged in memory only — the real
`.ini` files aren't touched until you click **💾 Export**. Export writes a
timestamped backup of each changed file before saving, so you can always roll
back. A **● Unsaved changes** indicator appears in the toolbar whenever there's
something pending, and opening a different mod folder with pending changes
asks for confirmation before discarding them.

## Limitation

- Texture only loads for mods that applied Slotfix/Stable Texture.
- Complex mods may not load correctly.

## Running it

Portable build — no install needed, just run the `.exe`. Requires 64-bit Windows
and the Evergreen WebView2 Runtime (already present on Windows 11, and delivered
to Windows 10 via Windows Update).

From source:

    pip install -r requirements.txt
    python src/viewer_app.py

To build the portable app yourself, see `src/build.py`. `src/features.ini` lets a
build hide the Export and toggle-authoring buttons if you want a read-only viewer.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).

