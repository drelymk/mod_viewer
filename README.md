# 3DMigoto Mod Viewer

3DMigoto Mod Viewer is a desktop app for opening character mods and inspecting
them in 3D without launching the game. Select a mod folder and the app reads its
active INIs, buffers, and texture bindings, reconstructs the model, and presents
it in an interactive viewport.

It supports mods made for ZZMI, GIMI, WWMI, and SRMI.

![3DMigoto Mod Viewer](https://github.com/drelymk/mod_viewer/blob/main/media/3DMigoto%20Mod%20Viewer.jpg)

## What the app can do

The viewer turns a mod's authored draw calls into a model you can orbit and
inspect. Components and meshes remain tied to their source INIs, while the
Inspector shows the selected material, texture assignments, and draw details.
Viewer-owned display controls make it easier to examine geometry and material
response without changing the mod.

The app also interprets the mod's controls. Toggle, menu, and PRESENT state can
be previewed to see how outfit variants and conditional meshes behave before
testing them in game. Individual meshes can still be isolated when you need to
inspect a hidden part or track down an incorrect condition.

Built-in diagnostics examine the active INIs and their resource graph. Reports
identify structural mistakes, suspicious control bindings, missing or unsafe
resources, unused declarations, and related file problems, with source locations
that can be opened directly in the INI editor. Diagnostics are read-only and can
still be viewed when a broken resource prevents the model from loading.

For authoring work, the app maintains a lossless in-memory edit session. The INI
editor, toggle tools, and recording workflow all update the same staged version,
so changes can be previewed before anything reaches disk. Export writes the
pending INIs together and creates timestamped backups; switching to another mod
warns before staged work is discarded.

Registered mod folders can be browsed as a small local library. Viewer-only
choices such as panel state, environment, and manual texture previews remain
separate from the mod's INIs.

## Compatibility and limitations

The app requires a WebGPU-capable system. Texture preview works best with mods
that use SlotFix/Stable Texture conventions. Unusual or highly customized mod
layouts may not be reconstructed completely; diagnostics remain available for
examining those folders.

## Running the app

The portable Windows build requires no installation. Run the executable on
64-bit Windows with the Microsoft Edge WebView2 Runtime available. WebView2 is
included with Windows 11 and is normally delivered to Windows 10 through Windows
Update.

To run from source:

```console
pip install -r requirements.txt
python src/viewer_app.py
```

To create a portable build:

```console
python src/build.py
```

Build output is written to `dist/`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
