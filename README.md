# VEX Perfect Mirror 1.0.4

Select **components** and a **mirror plane**, then click **OK**. Sketch recovery, fixed dots, origin recovery and cut-body visibility restoration run automatically.

## Where to find it

- **Design → VEX CAD Library → Mirror**, when the other VEX library add-in's tab is available. Only this add-in's panel/controls are added; its tab is preserved on uninstall.
- **Design → Solid → Create → Mirror** is also available, using the blue/teal mirrored-channel icon.
- **Last report** and **Help** remain available beside Mirror or in Solid/Create. The completion popout only says copy successful and gives the number of omitted relationships.
- No separate VEX MIRROR tab is created. If the library starts later, attachment is retried when a workspace or command activates.
- **Run on Startup defaults to enabled.** Existing installations may retain their earlier startup preference.

## Install or update

1. Extract `VEXPerfectMirror_v1.0.4.zip` into a permanent location. Keep the entire `VEXPerfectMirror` folder, including `resources`.
2. In Fusion, open **Utilities → Scripts and Add-Ins** (`Shift+S`). Stop any older `VEXPerfectMirror` entry before replacing its files.
3. Replace the old folder, or choose **+ → Script or add-in from device** and select the new `VEXPerfectMirror` folder. Select the folder directly containing its `.py` and `.manifest` files.
4. Turn **Run** on and confirm **Run on Startup** is checked. The manifest enables it for a new registration; Fusion can retain an old registration's preference, so an upgrade may require checking it once.
5. Use **Mirror** in **VEX CAD Library** or **Solid → Create**. If the library loads later, activate the Design workspace or start a command to attach the button.

No Python packages or additional plugins are needed. Only one registered copy should run. If you move/delete the registered folder, register its new location. To uninstall, stop the add-in and unlink its registration; existing mirrored geometry remains in the design.

## Use

1. Finish any active sketch/feature edit.
2. Start **Mirror** from VEX CAD Library or Solid/Create. Both selection fields start empty.
3. Select one or more **component instances or subassemblies**, including nested parts in the Browser. A selected subassembly includes its children automatically.
4. Choose a **construction plane or planar face**. No plane is preselected. Origin, offset, angled and component-face planes are supported.
5. Click **OK**. Review the result summary. Use **Last report** for any omitted relationships or notices.

The mirror is created in the root assembly. Undo reverses the command through Fusion's normal command transaction. Cancellation or a detected critical failure requests that Fusion abort the whole command rather than leave a partial mirror. Cancellation is checked between operations; an in-progress native Fusion operation must finish before cancellation can be handled.

## What this release implements

| Area | Behavior |
|---|---|
| Physical geometry | Fusion's native component reflection across the selected plane; not a rotated copy. |
| Selection and hierarchy | Multiple top-level or nested selections; selected descendants of selected parents are deduplicated; recursive sketch and visibility restoration in preserved child hierarchies. Duplicate selections are removed. |
| Mapping | One native mirror per selected occurrence gives a direct source/output association. Child/body matching uses reflected centers of mass, topology counts and volume checks where available. Ambiguous or inconsistent matches fail explicitly. |
| Sketch ownership | Recreated sketches belong to their corresponding mirrored component, not the root. |
| Point placement | Reads source points in assembly/root coordinates, reflects them across the plane, and converts into the target sketch. Verifies their placement after compute. |
| Point immobility | Ordinary result points receive Fix unless already fully constrained. Linked/projected points retain their driven state. |
| Origins and references | Includes sketch origins, recovers missing connected points, reuses matching target points, and tries genuine projections from fixed local helper points. Fixed fallbacks are counted. |
| Curves | Uses Fusion's `Sketch.copy` for curves and transferable constraints/dimensions. Applies the reflected local Z direction for 3D curves. Errors are reported and reject the operation. |
| Cut visibility | Restores individual body, occurrence and supported folder light-bulb states. |
| Offcut dots | Automatically trims standalone/origin dots against remaining visible stock when hidden bodies are present. Uses component-aligned bounds and a 3.5 mm margin. |
| Assembly relationships | Recreates internal rigid joints as as-built rigid joints, and internal rigid groups, preserving suppression. Reuses equivalent relationships already created by Fusion. |
| Grounding and metadata | Restores grounding, descriptions and part numbers. Output component names receive `[Mirror]`. |
| Results | Verifies point positions/states and checks body matching/visibility after relationships are solved. Gives a concise summary and a JSON diagnostic report. |
| UI and startup | Two selection fields, a dedicated tab, a Solid/Create shortcut, startup enabled, and no geometry-option checkboxes. |

### Purple and green dots

Color alone does not reliably identify a VEX dot's API role. In the user's v0.1.5 test, all 48 source points were fully constrained and six were fixed sketch origins; none reported `isReference` or `isLinked`.

The add-in inspects those API states and includes origins. Where possible, it creates a true projection from a fixed point in a hidden helper sketch inside the mirrored component. Fusion controls its projection color. Keep the helper sketches named `[Mirror anchors - keep hidden]`.

These are **local snapshots**, not reconstructed references to the original VEX geometry. If projection is unavailable, a fixed point preserves the position and immobility; it may appear green rather than purple. A nonzero fallback count is not a point-copy failure. Existing curve points are reused and locked without forcing their color.

### Automatic cut filter

The fixed 3.5 mm margin expands each visible body's bounding box in component coordinates. Larger or smaller values are not exposed in the simple UI. It does not change the bar's cut length, move points, or control mirror accuracy.

The filter runs only when directly owned hidden bodies exist, avoiding removal of construction points on uncut components. It keeps points if no visible directly owned stock can be evaluated. It uses bounds, not exact solid containment: hole-center points are intentionally valid. Curves and their structural points are not pruned. Models that use hidden bodies for purposes other than offcuts may need a different filtering policy in a future release.

## Limits that remain

This is a scoped 1.0 release, not implementation of every item in the proposed product roadmap.

- **Moving joints are not recreated.** Revolute, slider, cylindrical, pin-slot, planar and ball-joint motion, limits, offsets and motion links need additional work. The report names relevant joints omitted from the selection. A geometrically correct mirror may therefore have different mobility.
- Connections to unselected components or the root are omitted and listed. Standalone joint origins, contact sets and other advanced assembly relationships are not explicitly reconstructed.
- Sketches/points are snapshots. There is no live sketch synchronization, repair-existing-mirror command or conflict-aware update. Recreate the mirror after source edits. Native feature dependencies can behave differently from the added snapshots.
- Not every original constraint/dimension is recreated. Fusion transfers supported curve constraints; point locking guarantees the checked state rather than reproducing the full constraint graph.
- Sketch text is omitted with a notice. General 3D splines, large assemblies, surface-only parts, externally linked/read-only designs, and coincident/repeated nested geometry have not all received native test coverage. Mesh-containing selections are rejected.
- Nested component instances can be selected individually. Mirrored outputs are created in the root assembly. Selecting the document root is unsupported; select its component instances instead.
- Native Fusion may omit hidden offcut bodies. The add-in permits those omissions, reports them and still verifies every visible body. Matching checks can reject overlapping indistinguishable bodies/children. The add-in refuses to guess. Point reuse is within one sketch and may combine coincident source-point roles.
- Native body checking covers position, counts, volume and visibility; it is not a formal proof of every reflected surface or a collision/clearance analysis.
- No part substitution, manufacturability checking, bills of materials, cut lists, drawing/export workflow or assembly motion simulation.
- Custom properties/material/appearance overrides are left to the native mirror unless explicitly described above; complete metadata fidelity is not guaranteed.
- Automatic startup is configured and registration was checked, but a full Fusion restart was not used for testing.

## Reports and troubleshooting

Joint references and rigid-group members are captured before mirroring, because Fusion can invalidate their API references when the native feature computes. Unreadable source relationships are omitted and named in the report; the output can therefore be incomplete as a mechanism even when geometry is verified. Moving joints remain omitted.

**Last report** displays notices and the JSON path from the latest run in the current session. Reports are written to the operating system's temporary folder, contain point coordinates and object names, and are not uploaded. Copy a report somewhere permanent if you need it later.

An execution error requests transaction rollback. Correct the reported issue before rerunning. If Fusion leaves a command dialog open after an error, cancel it. Use a saved design version for your first full-mechanism test.

See `VALIDATION.md` for completed testing and its limits. Optional offline tests:

```sh
python3 -m unittest discover -s tests -v
```

## Autodesk API references

- [Mirror plane selection](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_MirrorFeatureInput.htm)
- [SketchPoint state and world geometry](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_SketchPoint.htm)
- [Fix property](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_SketchPoint_isFixed.htm)
- [Linked projection](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Sketch_project2.htm)
- [Curve and constraint copying](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Sketch_copy.htm)
- [As-built joint inputs](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_AsBuiltJointInput.htm)
- [Custom toolbar tabs](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ToolbarTabs_add.htm)
- [Command transaction abort](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_CommandEventArgs_executeFailed.htm)
