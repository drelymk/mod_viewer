# 3DMigoto Mod Viewer

3DMigoto Mod Viewer is a desktop app for opening character mods and inspecting
them in 3D without launching the game. Select a mod folder and the app reads its
active INIs, buffers, and texture bindings, reconstructs the model, and presents
it in an interactive viewport.

It supports mods made for ZZMI, GIMI, and WWMI.

![3DMigoto Mod Viewer](https://github.com/drelymk/mod_viewer/blob/main/media/3DMigoto%20Mod%20Viewer.jpg)

## What the app can do

### 1. Open the app and load a mod

1. Launch the portable executable, or follow [Running the app](#running-the-app)
   to start from source.
2. Click `Open Mod` and select the mod folder containing its INI, buffers, and
   textures. To preview a disabled mod, first enable the `Open disabled mod`
   checkbox beside the button.
3. For quick access later, open `Mod Library` on the left and click `+` or
   `Add Mod Folder`. Enter a name, use `Browse` to choose a folder, and click
   `Add`. Expand folders with their arrows, then click a mod folder's name to
   load it. The folder's menu lets you edit or remove its library entry.

### 2. Explore the model and its appearance

1. Drag with the left mouse button to orbit, drag with the right mouse button
   to pan, and scroll to zoom. Use `Reset`, `Turn`, or `Tilt` in the viewport
   toolbar to adjust the view, or click a navigation-gizmo axis to snap to it.
2. Open `Meshes` on the left. Expand components and use their checkboxes or
   individual mesh visibility buttons to isolate parts. `Reset mesh visibility`
   restores visibility from the current controls. Double-click a mesh name to
   rename it in the viewer.
3. Select a component or mesh and open `Inspector` on the right. Choose a
   material kind under `Material`, or leave it on `Auto`. For a selected mesh,
   choose a texture, `Automatic`, or `None` under `Texture`. Use
   `Manage textures` to add existing texture files and assign their maps for
   preview; these choices are saved separately from the INI bindings.
4. Hover over the viewport tools to find wireframe, outlines, smooth shading,
   toon shadows, glossy materials, grid, and emission bloom. Use the texture
   display menu to compare maps, and adjust key light or ambient occlusion.
   The top-bar environment and panel-opacity controls change lighting presets
   and panel transparency.

### 3. Preview existing controls and save combinations

1. Open `Controls` on the right and click the cycle buttons in `Key Toggle` to
   preview the mod's existing variants.
2. If `Menu Toggle` is available, click its buttons or images to cycle menu
   options. Move any supported shape sliders to preview shape changes.

#### Create and manage PRESENT combinations

PRESENT lets one shortcut switch several control values together. For example,
you can save one combination of outfit and accessory options, then another.

1. Set the combination you want using `Key Toggle`, `Menu Toggle`, and any
   supported shape sliders. PRESENT captures these control values.
2. If no PRESENT exists, open the `...` menu beside `Present` and choose
   `Add PRESENT`. Enter a `Key` such as `p` or `ctrl p`, optionally set a
   `Back key` for cycling backward, and click `Save`. This captures the current
   combination as `Present 1`.
3. Change the controls to another combination, click `New` under `Present`,
   enter a name, and confirm. Repeat for additional combinations, up to ten.
   If the values duplicate another present, the app asks whether to save it
   anyway.
4. Click the `Present` cycle button to move through the saved combinations.
   Check that the model and control values change together as expected.
5. To revise a combination, cycle to it, change the controls, and click
   `Update`. Check the present named in the confirmation, keep or edit its
   name, and confirm. To rename it without changing its values, use `Update`
   immediately after cycling to it.
6. To remove a combination, cycle to it, click `Delete`, and confirm. At least
   one present must remain. To remove the entire PRESENT cycle, use
   `...` > `Remove PRESENT key` instead.
7. To change the forward or backward shortcut, use `...` > `Edit key binding`.
8. When the combinations are ready, click `Export` to write the PRESENT
   changes to the participating INIs. The assigned shortcut cycles those
   combinations in game.

If no key or menu controls are available, create and record a Key Toggle in
the next section first. For a multi-INI mod showing `Unavailable`, read the
message under `Present`: use `Complete PRESENT` when offered to add missing
entries, then check every combination. Conflicting entries need review in
`View INI` before the cycle can be used.

### 4. Create and record a Key Toggle

Use a Key Toggle when you want one keyboard shortcut to switch between mesh
variants.

1. Open the `Controls` tab on the right.
2. In `Key Toggle`, click the `+` button.
3. Fill in the form:

   - Choose the `Ini file` containing the meshes you want to control.
   - Enter a display `Name` and the keyboard `Key` (for example, `1` or
     `ctrl 1`). Add a `Back key` if you want a shortcut for the previous
     position.
   - Enter a new `Variable name` without the `$` and at least two unique
     `Values`, separated by commas (for example, `0,1,2`). Leave
     `Default value` blank to use the first value.

4. Click `Save`. The toggle appears in the panel with a warning badge because
   it is not assigned to any meshes yet. Export becomes available once the
   toggle controls a mesh, or you delete the unused toggle.
5. Click the circular record button beside the new toggle.
6. At each displayed position, use the visibility buttons in the `Meshes`
   panel to show only the meshes that should be active for that position.
7. Click the toggle's cycle button to move to `Next position`, set its mesh
   visibility, and repeat until every position is recorded. The current
   position and variable values are shown in the toggle row.
8. Click `Save` in the recording row, or `Cancel` to abandon the recording.
   After saving, cycle through the toggle again to check the result. Review
   any reported skipped lines in `View INI` before exporting.

Use the pencil or delete button beside an existing toggle to edit or remove
it. Its record button lets you update which meshes appear at each position.
All these INI changes stay in memory until `Export`.

### 5. Inspect weights and preview secondary motion

Weight tools preview authored skinning; they do not rewrite the mod's weight
buffers.

1. Open the `Weight` tab on the right. The first time it opens, the app loads
   the model's weight data.
2. Click `Select bones` to open the bone list. Search by bone ID, optionally
   enable `Selected bones only`, and check the IDs you want. IDs are grouped
   by their source buffer, so select them under the source that owns them.
3. To discover influences at a point, click `Pick from model`, then click the
   model once. The list opens at `At picked point` with the nearby IDs; check
   the bones you want to select. Use `All bones` to return to the full list.
4. Turn on `Show Weight Heatmap` to see the selected bones' influence on the
   model.
5. With one or more bones selected, hold the right mouse button and drag the
   model to test the secondary motion. Under `Character physics`, adjust
   `Frequency (Hz)`, `Damping`, the response sliders, and optional `Gravity`
   or `Joint limits`. Use `Reset` to restart the physics preview.
6. Click `Save` beside the bone selection to store the selected IDs in the
   mod's `.mod_viewer.json`. Use `Load` to restore them or `Clear` to disable
   the selection. These choices are viewer metadata, not INI edits.

### 6. Adjust a mesh color and save it to a texture

Color sliders first create a viewer preview. `Save to Texture...` is the step
that modifies the source texture file.

1. In the `Meshes` panel, click a mesh row to select it. Click the row itself,
   not its visibility button.
2. In the `Inspector`'s `Texture` section, choose a diffuse texture (or leave
   `Automatic` if it resolves to one). Color editing is unavailable when the
   mesh has no diffuse texture or uses an Asset texture.
3. In the `Color` section, adjust `Hue`, `Saturation`, `Brightness`, and
   `Contrast`, then fine-tune `R`, `G`, `B`, `Tint`, and `Strength` as needed.
   Choose a `Tint` color and increase `Strength` to blend it in. The model
   updates immediately, and the preview settings are saved in `.mod_viewer.json`.
   Use `Reset Color` to remove the preview adjustment.
4. To bake the preview into a supported DDS texture, click `Save to Texture...`.
5. Review the texture and the list of meshes with color changes. Saving
   includes the changed meshes sharing that texture. Click `Save` to write
   the texture immediately, create a backup, and reload the result. The color
   controls reset after saving; `Reset Color` does not undo a texture save.

### 7. Preview original Assets or fill missing parts (optional)

1. Open `Assets` on the left and click `Add Asset Folder` or `+`. Choose its
   type (`ZZMI`, `GIMI`, or `WWMI`), browse to the extracted Asset folder, and
   click `Add`. Expand its folders and select an indexed Asset to preview it.
2. Use a folder's `ON`/`OFF` switch to include it in mod matching, and
   `Rebuild asset index` after changing its contents.
3. With a mod loaded and a matching original Asset available, click
   `Load missing parts` in the `Meshes` header to preview original components
   the mod does not replace. Click it again to remove them from the preview.

### 8. Check the INIs and export your edits

#### Read and act on Diagnostics

Diagnostics checks the current INIs, including staged edits. Opening the
report does not modify files, and it is available even when a resource problem
prevents the model from loading.

1. Click `Diagnostics` in the top toolbar to open `INI Diagnostics`. Read the
   error and warning totals at the top; each issue shows a `!` for an error
   or a triangle for a warning.
2. Choose a filter to focus the report:

   - `All`: every finding, including INI syntax and key-binding warnings.
   - `Errors`: errors across all categories, such as unreadable INIs or
     missing resource files.
   - `Conditions`: malformed expressions or broken `if`/`elif`/`endif`
     structure.
   - `Resources`: missing declarations or files, unsafe paths, invalid
     resource settings, and unused resource sections.
   - `Files`: asset files not declared by the active INIs.

3. Check the file counts in the summary. `Referenced assets` are files
   declared by active INIs; `inactive-only` files are referenced only by
   disabled INIs, and `viewer-only` files are used by viewer texture choices.
   Use these distinctions when investigating a file warning.
4. Read the issue's message, INI name, section, line number, and source text
   where shown. Issues are grouped by INI; findings without an INI appear
   under `Asset files`. Double-click an INI issue to open its reported line
   in the editor.
5. Make the correction and click `Save` in the editor. Reopen `Diagnostics`
   to review the refreshed report against the staged changes, then check the
   model preview again. For example, after correcting a resource's `filename`
   entry, check whether its missing-file error has cleared.
6. If Asset folders are configured, also check the Asset resolution summary
   for exact, partial, ambiguous, or missing matches. If it reports an
   unavailable index, return to `Assets` and use `Rebuild asset index` for
   the affected folder.

#### Edit an INI directly and export

1. Click `View INI` to open a file directly, choosing the INI from the menu
   when the mod has several. Edit its text and click `Save` to apply it to
   the same in-memory session used by toggles and recording.
2. Recheck `Diagnostics` and preview the affected controls or meshes.
3. Click `Export` to write pending INI changes and
   create timestamped `.BAK` backups. If a file fails to export, its changes
   remain pending. Export before switching mods or closing the app to keep
   your staged work; switching mods warns before discarding it.

In a narrow window, `View INI`, `Diagnostics`, and `Export` may be under the
toolbar's `...` menu. Bone selections and color previews are saved in
`.mod_viewer.json`; texture color saves use their own confirmation in step 6.

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
