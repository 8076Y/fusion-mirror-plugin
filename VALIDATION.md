# Validation — VEX Perfect Mirror 1.0.4

## 1.0.4 targeted checks

Focused mocked checks cover empty initial selections and preserved later choices, several selected instances in a populated design, nested/root-context selection, ancestor/child deduplication, concise completion text and idempotent external-tab/fallback mounting. Syntax and ZIP checks passed. No Fusion automation or full regression run was performed. Native nested-occurrence mirroring and actual external-tab placement remain unverified in this patch; native kernel limitations still cause a named failure rather than promoting selection to a different parent.

## 1.0.3 targeted check

Nine focused relationship tests passed, including a simulated InternalValidationError from an invalidated joint endpoint, unreadable-source reporting, group snapshots, existing rigid-joint duplicate checks, rigid creation, motion/external omission and cancellation. Syntax/package checks passed. No native Fusion or broad regression run was performed. Completion and moving behavior on the user's real mechanism remain unverified; moving joints are unsupported.

## 1.0.2 targeted check

Six focused mocked cases passed: omitted hidden offcut, retained hidden-body visibility restoration, missing visible stock rejection, incorrect position rejection, extra output rejection and nested cut-bar matching. Syntax and ZIP checks passed. No native Fusion run or broad regression was performed for this patch. The actual failing VEX bar remains unverified.

## 1.0.1 targeted check

Eight focused selection tests passed: assembly instance/deduplication, unique component resolution, ambiguous instance rejection, root rejection, wrong-type diagnostics, stale selection diagnostics, nested-selection rejection and raw selection handling through the command event. Syntax and package checks also passed. No Fusion automation or full regression run was performed for this hotfix. The user's lift has not been reproduced or verified.

## Inherited 1.0.0 validation

The results below apply to 1.0.0, before the selection-only hotfix.

## Offline tests

52 automated regression tests passed on 2026-10-03. Tests include randomized oblique-plane reflection/involution checks; offset and origin planes; assembly-context plane conversion; null connection collections; fixed/reference/origin points; projection fallback cleanup; point reuse; nonplanar points; geometry ambiguity; rotated cut filtering; rigid-joint/group handling; selection validation; command handler lifetime; startup manifest; and failure/cancellation requesting Fusion transaction abort.

The mocked integration tests exercise the mirror execution flow and deliberately inject point-copy failure. They do not emulate Fusion's modeling kernel or prove transaction behavior.

## Native Autodesk Fusion tests

Eight cases were executed inside the installed Fusion application on macOS, using isolated newly created documents. Each document was closed without saving after the test, and the pre-existing document was restored. These tests used synthetic BRep stock with two bodies, one hidden body and sketch points, rather than the user's actual VEX mechanism.

| Case | Result | Point checks passed | Offcut points removed | Additional result |
|---|---|---:|---:|---|
| Root YZ plane | Passed | 3 | 1 | Body visibility restored |
| Offset XZ plane | Passed | 3 | 1 | Non-origin reflection |
| Angled construction plane | Passed | 3 | 1 | Oblique reflection |
| Rotated source with circle and line | Passed | 6 | 1 | Curve copy and associated points |
| Selected component face | Passed | 3 | 1 | Assembly-context plane |
| Nested subassembly | Passed | 3 | 1 | Child hierarchy and sketch repair |
| Two selected components with rigid joint | Passed | 3 | 1 | One rigid joint recreated |
| Two selected components with rigid group | Passed | 3 | 1 | One rigid group recreated |

All eight reports had zero point, sketch or curve failures and no warnings. The runtime checks also revalidated body correspondence/visibility after compute.

## Registration

The packaged add-in folder was registered in Fusion. The Scripts and Add-Ins dialog showed version 1.0.0, **Run on Startup checked by default**, and **Run enabled** after starting, without a startup error. A full application restart was not performed.

## Remaining test coverage

The new toolbar and two-input dialog need a full visual/manual workflow check, beyond registration and mocked event testing. Actual cancellation/rollback after native mutation, Windows execution, a full restart, very large models, varied 3D curves and advanced assembly arrangements remain unverified. No native motion transfer is claimed. The actual user's VEX model was previously demonstrated working with v0.1.5; this release's native tests used temporary synthetic designs.
