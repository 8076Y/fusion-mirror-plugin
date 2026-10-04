# VEX Perfect Mirror

An Autodesk Fusion 360 add-in for mirroring VEX robot parts and assemblies while preserving sketch attachment points and cut-part visibility.

Fusion’s native component mirror handles the physical geometry. This plugin adds sketch recovery, fixed attachment points, hidden-body visibility restoration, and supported rigid assembly connections.

## Features

- **True geometric reflection** across a construction plane or planar face, including offset and angled planes.
- **Component and assembly selection** with support for multiple instances and individual nested parts.
- **Sketch attachment-point recovery** inside the corresponding mirrored components.
- **Fixed mirrored points** that cannot be dragged away from their verified positions.
- **Reference and origin-point recovery**, with fixed snapshots when projected references cannot be recreated.
- **VEX cut-part handling**, including hidden-body visibility restoration and automatic filtering of offcut attachment points.
- **Rigid joint and group restoration** where component references are readable and both sides are selected.
- **Duplicate-constraint checks** to avoid adding redundant rigid connections.
- **VEX CAD Library toolbar integration**, with a Solid → Create shortcut when that tab is unavailable.
- **Automatic startup** and a simple dialog with two inputs: components and mirror plane.

> **Motion support:** Moving joints are not reconstructed. Mirrored geometry can be correct while the resulting assembly has different movement or remaining degrees of freedom.

## Installation

1. Download and extract the release ZIP.
2. Keep the complete `VEXPerfectMirror` folder in a permanent location.
3. In Fusion, open **Utilities → Scripts and Add-Ins**, or press **Shift+S**.
4. Upload the entire folder.
5. Start the add-in and enable **Run on Startup**.

No additional Python packages are required.

### Updating

Stop the add-in before replacing its files, then start it again. Keep only one registered copy running.

If the registered folder moves, update its registration in Scripts and Add-Ins.

## Usage

1. Finish any active sketch or feature edit.
2. Open **Mirror** from **VEX CAD Library → Mirror** or **Solid → Create**.
3. Select the component instances or subassemblies to copy.
4. Select a construction plane or planar face.
5. Click **OK**.

Both selection fields start empty. Selecting a subassembly includes its children; selecting a parent and its child together does not copy the child twice.

Mirrored outputs are created in the document’s root assembly. Select component instances rather than the document root.

The completion message confirms success and shows how many relationships could not be copied. **Last report** provides the detailed reasons. Fusion’s normal **Undo** command removes a completed operation.

## Sketch Points and Cut Parts

Mirrored sketch points are independent snapshots. Their reflected positions are checked, and recreated points are fixed.

Purple reference points may become green fixed points when a projected reference cannot be recreated. This preserves their position and immobility, but does not preserve a live connection to the original geometry.

The automatic cut filter uses a **3.5 mm margin** around visible-body bounds when hidden bodies are present. This helps retain hole-center attachment points near cut stock. The margin does not change cut length or mirror accuracy.

Hidden offcut bodies omitted by Fusion’s native mirror are allowed, provided the visible source bodies pass the matching checks.

## Limitations

- Revolute, slider, cylindrical, pin-slot, planar, and ball-joint motion is not reconstructed.
- Joint limits, motion links, contact sets, and standalone joint origins are not explicitly copied.
- Unreadable or unsupported relationships are listed as omitted.
- Sketches do not synchronize automatically after source edits.
- Original sketch dimensions and the complete constraint graph are not reproduced.
- Sketch text is omitted, and mesh-containing selections are unsupported.
- External links are not broken automatically. Imported or linked assemblies may encounter Fusion API restrictions.
- Overlapping, indistinguishable geometry may be rejected when component or body matching is ambiguous.
