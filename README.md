# 3DMigoto Mod Viewer

3DMigoto Mod Viewer is a desktop app for opening character mods and inspecting
them in 3D without launching the game. Select a mod folder and the app reads its
active INIs, buffers, and texture bindings, reconstructs the model, and presents
it in an interactive WebGPU viewport. Preview variants and supported animations,
inspect textures and skin weights, pose the model, and stage INI or mesh edits.

It supports Zenless Zone Zero (ZZMI), Genshin Impact (GIMI), and Wuthering Waves
(WWMI) mods, with conservative support for Honkai: Star Rail (SRMI) mods.

![3DMigoto Mod Viewer](https://github.com/drelymk/mod_viewer/blob/main/media/3DMigoto%20Mod%20Viewer.jpg)

## What the app can do

The instructions below use the English UI labels. Use `Language` in the top
toolbar to switch between English, Simplified Chinese, Japanese, Korean,
Spanish, and Russian. Optional actions can be hidden in portable builds; source
runs enable all features. See [Compatibility and limitations](#compatibility-and-limitations).

### 1. Open the app and load a mod

1. Launch the portable executable, or follow [Running the app](#running-the-app)
   to start from source.
2. Click `Open Mod` and select the mod folder containing its INI, buffers, and
   textures. If available in your build, enable the `Open disabled mod`
   checkbox beside the button to preview a disabled mod.
3. To preview a compressed mod, open the arrow beside `Open Mod` and choose
   `Open Archive...`. Select a ZIP, 7z, or RAR file without manually extracting
   it. ZIP works directly; 7z and RAR require an installed 7-Zip. Archive previews
   are read-only: controls and diagnostics work, but changes cannot be exported
   or saved back to the archive. Extract the mod to a folder to edit it.
4. For quick access later, open `Mod Library` on the left and click `+` or
   `Add Mod Folder`. Enter a name, use `Browse` to choose a folder, and click
   `Add`. Expand folders with their arrows, then click a mod folder's name to
   load it. Supported archives also appear in the library. The folder's menu
   includes `Open Folder` to open its location in the file manager; registered
   library roots also have `Edit` and `Remove` actions.

### 2. Explore the model and its appearance

1. Drag with the left mouse button to orbit, drag with the right mouse button
   to pan, and scroll to zoom. Use `Reset`, `Turn`, or `Tilt` in the viewport
   toolbar to adjust the view. Drag the navigation gizmo to orbit, click an
   axis to snap to it, or use its toolbar button to show or hide the gizmo.
2. Open `Meshes` on the left. Expand components and use the INI, component, or
   mesh eye buttons to show or hide their meshes. `Reset mesh visibility`
   restores visibility from the current controls. Right-click a mesh row or the
   mesh in the viewport and choose `Rename` to change its viewer name; press
   Enter to confirm or Escape to cancel.
3. Click a mesh row or the model to select a mesh. Shift-click selects a range of
   expanded rows in MESHES; Ctrl+Shift-click adds that range to the selection.
   Ctrl-click toggles meshes in the selection, and Ctrl-drag in the viewport adds
   meshes crossing the selection rectangle. Double-click a mesh row or the model
   to frame it and open `Inspector`. Ctrl+A in MESHES selects all mesh rows,
   including hidden meshes and collapsed groups; in the viewport it selects
   visible meshes. Press `F` to frame the selection or `H` to hide it. Clicking
   a selected mesh's eye in MESHES or Inspector shows or hides the entire
   selection together, using that eye's new state; an unselected mesh's eye
   affects only that mesh. Control changes update only meshes wired to the
   changed visibility variables, preserving other manual visibility choices.
   Right-click the selection and choose `Isolate Selected` to focus on those
   meshes; use `Exit Isolation` in the `Meshes` header to restore the view.
   Isolation is available even when mesh editing is hidden.
4. Select a component or mesh and open `Inspector` on the right. Choose a
   material kind under `Material`, or leave it on `Auto`. For a selected mesh,
   choose a texture, `Automatic`, or `None` under `Texture`. Use
   `Manage textures` to add existing texture files and assign their maps for
   preview; these choices are saved separately from the INI bindings. Texture
   choices include discovered DDS, PNG, JPG, and JPEG files, including images
   not declared by the INIs.
5. Use the viewport tools to toggle wireframe, silhouette outlines, smooth
   shading, `Toon shadows`, and the grid. Click `Glossy materials` or
   `Ambient occlusion` to adjust their strength from 0–100%. The texture display
   menu offers `All maps`, `Diffuse and NormalMap`, `Diffuse only`, and
   `No textures` for comparing the material response. `Emission bloom` is
   available when a supported GlowMap is detected.
6. Click `Key light` to adjust its intensity, including turning it off at 0%.
   Drag the light marker in the viewport to move it across the view; hold Shift
   when starting the drag to move it toward or away from the camera.
7. Use the top-bar environment menu to choose `Default`, `Studio`, `Indoor`,
   `Outdoor`, or `Sunset`, and the panel-opacity control to adjust panel
   transparency. Language, environment, panel opacity, and viewport tool
   preferences persist between launches; camera and model orientation do not.

#### Inspect UVs and texture tiling

1. Select a mesh or separated part and expand `UV` below `Color` in
   `Inspector`. The view overlays its triangle wireframe on the current diffuse
   texture. Meshes without a diffuse texture still show their UV layout.
2. Click `Expand UV view` to display it over the viewport. The view follows
   mesh selection and texture changes; use its close button or click the expand
   button again to return to the inline view.
3. UVs outside the 0–1 region display the repeated texture across their full
   bounds, matching the model's texture tiling. During `Separate by Selection`,
   selected faces are highlighted in the UV view as well.

UV inspection is read-only and remains available when mesh editing is hidden.

### 3. Preview existing controls and save combinations

1. Open `Controls` on the right and click the cycle buttons in `Key Toggle` to
   preview the mod's existing variants.
2. If `Menu Toggle` is available, click its buttons or images to cycle menu
   options. Move any supported shape sliders to preview shape changes.

Recognized animations play automatically while their mod conditions are active.
The viewer supports baked mesh animation, supported GIMI compute-driven pose
and shape animation, and narrow WWMI sparse shape animation patterns. Toggle
and menu changes can activate or stop these tracks. Custom shader programs may
remain static; this is a reconstruction of supported patterns, so playback can
differ from the game.

#### Pause animation or inspect a frame

1. While a supported animation is active, click `Pause animation` to freeze
   playback, or `Play animation` to resume it. The button appears in the
   viewport toolbar or alongside the frame slider when one is available.
2. For baked animations sharing one synchronized timeline, drag the
   `Animation frame` slider to inspect a specific frame. Seeking pauses
   playback and shows the current frame counter; click `Play animation` to
   continue from that frame.
3. Compute animations and independent timelines provide Play/Pause without
   frame seeking. Playback controls appear only for visible animated geometry
   that is not currently controlled by Rig or Physics.

Pausing and seeking affect only the preview. They do not change the mod's
control values or write animation data.

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

#### Create a show/hide toggle from selected meshes

1. Select one or more authored meshes from the same INI. Ctrl-click adds meshes
   to the selection.
2. Right-click a selected mesh in the viewport or `Meshes` panel and choose
   `Create Toggle` when offered.
3. Review the prefilled name, variable name, and suggested unused key. The INI
   and two values (`0,1`) are fixed for this shortcut. Click `Save` to create a
   toggle already assigned to the selected draws.
4. Cycle it in `Controls` to check the show/hide result, then use `Export` to
   write the change. The action is available only for supported editable draws;
   finish any active recording or mesh edit first.

#### Create a custom cycle and record its mesh visibility

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

### 5. Separate and merge mesh parts (when enabled)

Mesh editing is optional and may be hidden in portable builds. It requires a
writable mod folder and supported authored draws; archive previews and original
Assets cannot be edited this way.

1. Select a mesh and use the edit icons to the left of the eye in the Inspector
   header, or right-click a mesh row or the mesh in the viewport, and choose a
   separation method. Hover an icon for its action name or disabled reason:

   - `Separate by Loose Parts`: adjust `Connection tolerance` from 0 to 0.01
     and click `Separate`. Zero uses exact vertex positions; a small tolerance
     can connect nearby positions into the same part.
   - `Separate by Selection`: click faces to select them, Ctrl-click to add or
     remove faces, Ctrl+A to select every face of the mesh or part being edited,
     or Ctrl-drag to select faces crossing a rectangle. Leave at least one face
     unselected, then choose `Apply Selection`. Inspector also
     provides `Select Connected Faces` and `Invert Selection`. While selecting
     faces, use the mode icons to the right of the left-panel tabs. These appear
     only during Separate by Selection and control both clicks and Ctrl-drag
     rectangles: `Set` replaces
     the selection, `Extend` adds, `Subtract` removes, `Difference` toggles hit
     faces, and `Intersect` keeps only previously selected hits. Ctrl-click
     still toggles faces independently of the chosen mode. Clicks select the
     nearest visible face; enable `Select through (click)` to include faces
     behind other faces. Rectangles always select through. `Cancel Selection` or
     Escape exits without splitting.

2. Right-click a mesh in the viewport or MESHES panel and use `Isolate Selected`
   to show selected meshes or parts and hide all others. The action stays
   available and can be repeated for a different selection; use `Exit Isolation`
   in the `Meshes` header to restore the view. To combine parts, Ctrl-click or
   Shift-click two or more parts from the same original
   mesh and choose `Merge Meshes`. Parts from different sources cannot be merged.
   Undo/Redo icons beside the component's `Edited` badge restore recent preview
   layouts and face selections. Ctrl+Z undoes and Ctrl+Y redoes; text inputs keep
   their normal editing shortcuts. History is cleared when changes are applied,
   canceled, or the model is reloaded.

   Isolation remains available when mesh editing is hidden; separation, merging,
   and preview history require mesh editing to be enabled.
3. Inspector shows part, mesh-selection and face-selection counts, plus preview
   or staged status. Choose `Apply Mesh Changes` beside the component's `Edited`
   badge, or select the component and use its Inspector header or context menu,
   to stage the new draw ranges and index-buffer layout in memory and reload
   the preview. `Cancel Mesh Changes` abandons the unapplied layout.
4. Check the reloaded parts, then click `Export` to write the staged buffer and
   INI changes with backups. Apply or cancel the current layout before starting
   another mesh edit; unapplied layouts block Export.

Separation, merging, and `Apply Selection` affect the preview first.
`Apply Mesh Changes` stages an edit, and `Export` writes it to disk.

### 6. Inspect weights and preview secondary motion

Weight tools preview authored skinning; they do not rewrite the mod's weight
buffers.

1. Open the `Weight/Rig` tab on the right. The first time it opens, the app loads
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
   model to test the secondary motion. Enable optional `Gravity`, then open
   `Advanced Settings` under `WEIGHT` to adjust `Frequency`, `Damping`, the
   response sliders, and `Joint limits`. Use `Reset physics` to restart the
   motion without resetting a manual pose.
6. Click `Save` beside the bone selection to store the selected IDs in the
   mod's `.mod_viewer.json`. Use `Load` to restore them or `Clear` to disable
   the selection. These choices are viewer metadata, not INI edits.

### 7. Pose the model and save Rig presets

The `RIG` section in `Weight/Rig` provides an inferred rig and a fitted humanoid
control rig when usable skin weights and geometry are available. These are
viewer posing tools; they do not recover the game's original named skeleton or
write poses into the mod's buffers.

1. Choose a `Selected Joint`, or use `Pick from model` in the `RIG` section to
   select a joint from the viewport. Drag the rotation gizmo to pose it.
   `Reset Joint` clears that joint's manual rotation; `Reset Pose` clears the
   manual pose.
2. Open `Advanced Settings` under `RIG` to set `Rotation snap`, change the
   root with `Set selected as root`, or enable `Enable Inverse Kinematics`.
   With IK enabled, select a supported hand or foot control point and drag its
   move gizmo to pose the limb.
3. If the fitted control points need correction, choose `Main Rig` > `Edit Rig`.
   Click a point, move it, and click again to release it. Use `Save` to store
   the corrections or `Cancel` to abandon them. `Reset Rig` removes saved
   corrections after confirmation.
4. Use `Save` under `Pose presets` to name and store a pose. Choose it from
   the preset list to apply it later; `Rename` and `Delete` manage saved entries.
   Presets are saved in `.mod_viewer.json` and are not applied automatically
   when loading the mod. They store pose and root choices, not live physics
   motion.

Rig posing and weight-driven physics can work together. Geometry-changing shape
controls rebuild the affected rig, and Rig/Physics temporarily takes control
of affected geometry from animation playback. Inferred joints and connections
can differ from the authored skeleton, so some poses may stretch unexpectedly.

### 8. Adjust a mesh color and save it to a texture

Color sliders first create a viewer preview. `Save to Texture...` is the step
that modifies the source texture file.

1. In the `Meshes` panel, click a mesh row to select it. Click the row itself,
   not its visibility button.
2. In the `Inspector`'s `Texture` section, choose a diffuse texture (or leave
   `Automatic` if it resolves to one). Color editing is unavailable when the
   mesh has no diffuse texture or uses an Asset texture.
3. In the `Color` section, adjust `Hue`, `Saturation`, `Brightness`, and
   `Contrast`, then fine-tune `R`, `G`, `B`, and `Tint` as needed. Choose a
   `Tint` color to recolor the texture while preserving its shading; use
   `Clear` to disable it. Brightness keeps its 0–400% range with 100% centered
   on the slider. The model updates immediately, and the preview settings are
   saved in `.mod_viewer.json`. Use `Reset Color` to remove the preview
   adjustment.
4. To bake the preview into a BC7 UNORM or BC7 sRGB DDS texture owned by the
   mod, click `Save to Texture...`. Other texture formats remain preview-only.
5. Review the texture and the list of meshes with color changes. Saving
   includes the changed meshes sharing that texture. Click `Save` to write
   the texture immediately, create a backup, and reload the result. The color
   controls reset after saving; `Reset Color` does not undo a texture save.

### 9. Preview original Assets or fill missing parts (optional)

1. Open `Assets` on the left and click `Add Asset Folder` or `+`. Choose its
   type (`ZZMI`, `GIMI`, or `WWMI`), browse to the extracted Asset folder, and
   click `Add`. Expand its folders and select an indexed Asset to preview it.
2. Use a folder's `ON`/`OFF` switch to include it in mod matching, and
   `Rebuild asset index` after changing its contents.
3. With a mod loaded and a matching original Asset available, click
   `Load missing parts` in the `Meshes` header to preview original components
   the mod does not replace. Click it again to remove them from the preview.

Direct Asset previews and missing-part fills leave the original files untouched.
Filled parts last only for the current session and are removed on reload or
when switching models. Automatic filling requires a unique matching Asset.

### 10. Check the INIs and export your edits

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
3. Click `Export` to write pending INI changes and applied mesh-buffer changes
   with timestamped `.BAK` backups. A failed buffer write blocks its dependent
   INIs, and failed changes remain pending for retry. Export before switching
   mods or closing the app to keep your staged work; switching mods warns before
   discarding it.

In a narrow window, `View INI`, `Diagnostics`, and `Export` may be under the
toolbar's `...` menu. Texture color saves use their own confirmation in step 8.

#### Export the visible model for Blender

Use the arrow beside `Export` and choose `Blender / glTF (.glb)…`, then select
a destination in the native save dialog. In a narrow window this option is in
the toolbar's `...` menu. Import the result in Blender with
`File → Import → glTF 2.0 (.glb/.gltf)`. The main `Export` button and the
dropdown's `Save Mod Edits` action still save pending mod edits as described above.

GLB export works even when no mod edits are pending. It captures the currently
visible draws and loose parts as separate named objects, including their current
pose, shape, animation frame, orientation, normals, and UVs. Temporary Weight
heatmap colors are excluded. Standard materials include diffuse textures,
supported RGB normal maps, and supported emission maps. DDS material maps are converted at their original
resolution; textures are embedded so the file can be moved independently.

Only Blender-ready images are embedded. Bound texture identities for all six
roles are retained in `materials[].extras.modViewer.textures`, without embedding
duplicate original resource bytes. Packed maps remain role keys for reference;
Blender does not automatically reproduce their shader behavior. Mesh identity,
source draw provenance, material profiles, and color adjustment settings are
preserved in `extras.modViewer`. Color adjustments and game-specific packed
shader effects are not baked into standard materials yet.

Authored skin weights are loaded on export and imported into Blender as numbered
vertex groups, scoped to each exact Blend source. Up to eight influences per
vertex are retained; duplicate IDs combine and weights normalize for glTF.
Zero-weight vertices use an `Unweighted` placeholder group. Large authored bone
IDs retain their numeric group names through a compact export palette. Export
uses neutral placeholder joints to carry the groups while preserving the current
mesh pose; these joints do not reconstruct game bone positions or hierarchy.
Weight loading does not activate the viewer's Rig or Physics. If advertised
weights cannot be loaded, export reports an error instead of silently omitting them.

Editable game rigs, animation tracks, shape keys, hidden toggle alternatives, and
viewer lighting/debug effects are not exported. Missing or unconvertible textures
are reported after saving; geometry and available resources still export. A GLB
snapshot does not commit staged INI or buffer edits. Export remains unavailable
for compressed mods and direct Asset previews.

### What is saved, and when

| Change | Save behavior |
| --- | --- |
| INI text, Toggle CRUD/Record, and PRESENT combinations | Staged in memory until `Export`. |
| Mesh separation and merging | Preview-only until `Apply Mesh Changes`, then staged until `Export`. |
| Mesh names, material/texture choices, and color previews | Saved as viewer metadata in `.mod_viewer.json` for writable mod folders. |
| Bone selections, Rig corrections, and pose presets | Saved to `.mod_viewer.json` by their own `Save` actions. |
| `Save to Texture...` | Writes the supported source DDS immediately after confirmation, with a backup. |
| Language, environment, panel opacity, and viewport tool preferences | Saved globally as app settings. |

Archive previews cannot persist edits or metadata. Direct Asset texture choices,
manual visibility, camera/model orientation, and live physics motion are
session-only.

## Compatibility and limitations

The app requires a WebGPU-capable system. Texture preview works best with mods
that use SlotFix/Stable Texture conventions. Unusual or highly customized mod
layouts may not be reconstructed completely; diagnostics remain available for
examining those folders.
Original Asset indexing currently offers ZZMI, GIMI, and WWMI types.

Animation, shapes, Rig tools, mesh editing, and texture saving depend on the
recognized source layout. Texture saving supports mod-owned BC7 DDS files only
and may reject overlapping color edits or a file also used as an auxiliary map.
Password-protected archives are not supported; 7z and RAR previews require
7-Zip to be installed.

## Running the app

The portable Windows build requires no installation. Run the executable on
64-bit Windows with the Microsoft Edge WebView2 Runtime available. WebView2 is
included with Windows 11 and is normally delivered to Windows 10 through Windows
Update.

To run from source:

```console
pip install -r src/requirements.txt
python src/viewer_app.py
```

To create a portable build:

```console
python src/build.py
```

Build output is written to `dist/`.

Before building, `src/features.ini` can hide optional Export, Toggle editing,
disabled-mod loading, and mesh-editing actions in the portable executable. The
checked-in settings hide disabled-mod loading and mesh editing. These are
build-time UI settings; running from source enables every feature.


## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
