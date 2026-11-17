# Colour Grading page: design

Date: 2026-11-17. Target release: Operator Board 0.9.0.
Source spec: `../FUTURE_UPDATES.md` section 3 (beside the repo). Decisions below were settled with the owner in conversation; anything marked *(builder's call)* was not.

## Intent

Virtual production operators need to grade the live Unreal image (the LED wall and the camera's inner frustum) quickly during a shoot, without digging through the nDisplay root actor's details panel. The page should work and look like **Assimilate Live FX**'s colour grading, so a colourist feels at home, while every control stays clearly tied to the Unreal setting it changes.

Success: an operator picks "the whole wall" or "the ICVFX camera", drags a Gain wheel, sees the wall change live, saves the look as "Day ext warm", recalls it from a Stream Deck key later, and every change is in the change log.

## Goals

1. A **Colour Grading** page, placed in the side menu straight after Remote Objects.
2. Four kinds of target, in this priority (Live FX order), all in the first release, listed in the target picker in this order:
   1. **nDisplay overall image**: the root actor's Entire Cluster colour grading, and each Per-Viewport grading entry.
   2. **ICVFX camera**: each ICVFX camera component's inner frustum grading (All Nodes; Per-Node entries).
   3. **Post Process Volumes**: every PPV in the level (unbound/global and local ones).
   4. **Colour Correct Regions and grading spheres**: `ColorCorrectRegion` actors (sphere, box, cylinder, capsule) and `ColorCorrectWindow` actors.
3. **Controls** (the "Primaries" view), each titled in colourist terms with the Unreal name in small italics underneath:

   | Title | *Unreal underneath* | Widget |
   |---|---|---|
   | Lift | *Shadows · Offset* | colour wheel + brightness slider |
   | Gamma | *Global · Gamma* | colour wheel + brightness slider |
   | Gain | *Global · Gain* | colour wheel + brightness slider |
   | Offset | *Global · Offset* | colour wheel + brightness slider |
   | Saturation | *Global · Saturation* | colour wheel + brightness slider |
   | Contrast | *Global · Contrast* | colour wheel + brightness slider |
   | Temperature | *White Balance · Temp* | slider |
   | Tint | *White Balance · Tint* | slider |

   An **"Advanced: tonal ranges"** tab shows the full Unreal sets: Shadows, Midtones and Highlights, each with Saturation, Contrast, Gamma, Gain and Offset wheels, plus the shadows max / highlights min / max range settings where the target has them.
4. **Wheel handling** (as in Live FX / Resolve): drag the puck to set colour; drag the slider underneath, or scroll on the wheel, for brightness (the Y / luminance part of Unreal's Vector4); hold **Shift** for fine control; **double-click** resets that wheel. Under each wheel, numbers you can type into, with a **cycle button RGB ↔ Hex**:
   - RGB shows Unreal's real values (any range, e.g. `1.12 / 0.98 / 0.95`);
   - Hex shows only the tint colour (hue and strength of the puck, brightness left out), e.g. `#FFE8DD`; typing a hex sets the colour and leaves the brightness slider alone.
5. **Live updates while dragging**: changes go to Unreal continuously (latest value, about 20 per second). One change log entry per gesture (drag, scroll burst, typed value, reset), not per mouse move.
6. **Bypass** (A/B against the ungraded image) for the selected target, **Reset** per wheel and for the whole grade of a target, and **Undo** through the change log (existing Remote Objects Undo pattern, `ctx.apply_setting`).
7. **Looks**: named grades saved **per project**. A look holds the grade of every target together; when saving, the operator can untick targets to leave out. Recalling a look sets every target it holds. Looks can be added as an item in **saved settings presets**, and each take's log entry records which look was active.
8. **Actions** (single ActionRegistry, so Stream Deck, hotkeys, HTTP API and custom page buttons all get them):
   - recall a look;
   - bypass on / off / toggle;
   - reset (a target, or all);
   - **Nudge grade**: one action with parameters control (any wheel's brightness, a wheel's colour direction, Saturation, Contrast, Temperature, Tint), amount (+/-) and target (default "the target selected on the Colour Grading page", or a fixed target). Default amounts: Temperature ±100 K, Tint ±0.05, Lift / Gamma / Gain / Offset brightness ±0.02, Saturation and Contrast ±5 %. The button editor gets an editor for these parameters.
   - The default Stream Deck layout gains a **Grading** page with the most useful nudges, Bypass and Reset.
9. **Custom Pages**: every wheel, slider, the target picker, the looks list and the Bypass / Reset buttons are offered individually in the Custom Page editor (each is its own widget attribute inside a titled `Card`), as well as the whole panels.
10. **Unreal versions**: built and probed live on UE 5.8, then 5.7 and 5.6 (installed on this PC). 5.2–5.5 are checked against Epic's published source and documentation only and marked unverified. The owner will install every version (and 5.9, which is not targeted yet) before publishing and test them all.

## Non-goals (this release)

- MIDI knobs and faders (CC) driving wheels: goes in `docs/ROADMAP.md` as the next step. Today's MIDI support reads pads only.
- Curves (Unreal's post process has no RGB curves to drive).
- Grading anything that isn't Unreal (that is the Operator Listener, priority 4).
- Creating new grading targets (new PPVs, new per-viewport entries, new CCRs) from Operator Board. The page grades what exists in the level. *(builder's call; reasonable default, revisit if the owner asks)*

## Anti-goals

- The wall must never jump to a wrong grade: a failed or partial send is reported, never silently left half-applied across nodes.
- Never change a stage the operator didn't point at (the probe incident that moved the nDisplay root in a live session must not repeat).
- No proprietary assets or Live FX artwork; the look is "in the style of", drawn by us.

## Constraints

- Python 3.9 compatible; core packages must not import Qt (only `ui/` and `qt.py`).
- MIT; anything bundled must be open source.
- README lists shipped features only.
- Unreal probes: ports 30050+, checked free with `netstat -ano`, answering PID confirmed as the probe's own before any change.
- Owner merges PRs; commit/push only when asked.

## What Unreal exposes (from UE 5.6 / 5.7 / 5.8 headers on this PC)

Unreal property names below come from the engine headers; **every Python access path must still be confirmed by a read-only probe in a real 5.8 editor and a -game node before relying on it**.

- **nDisplay** (`DisplayClusterConfigurationTypes_Postprocess.h`, `_ICVFX.h`; identical file length in 5.6, 5.7, 5.8):
  - Root actor config data → `StageSettings` (`FDisplayClusterConfigurationICVFX_StageSettings`): `EnableColorGrading`, `EntireClusterColorGrading` (`bEnableEntireClusterColorGrading`, `ColorGradingSettings`), `PerViewportColorGrading[]` (each: `bIsEnabled`, `bIsEntireClusterEnabled`, `ColorGradingSettings`, `ApplyPostProcessToObjects` = viewport names; `Name` is **editor-only**, so on -game nodes entries are identified by index + viewport list).
  - ICVFX camera component → `CameraSettings`: `EnableInnerFrustumColorGrading`, `AllNodesColorGrading` (`bEnableInnerFrustumAllNodesColorGrading`, `bEnableEntireClusterColorGrading`, `ColorGradingSettings`), `PerNodeColorGrading[]` (each: `bIsEnabled`, `bEntireClusterColorGrading`, `bAllNodesColorGrading`, `ColorGradingSettings`, `ApplyPostProcessToObjects` = node names).
  - `ColorGradingSettings` (`FDisplayClusterConfigurationViewport_ColorGradingRenderingSettings`): `WhiteBalance` (`TemperatureType`, `WhiteTemp`, `WhiteTint`), `Global` / `Shadows` / `Midtones` / `Highlights` (each `Saturation`, `Contrast`, `Gamma`, `Gain`, `Offset` as Vector4 with `bOverride_*` flags), `ColorCorrectionShadowsMax`, `ColorCorrectionHighlightsMin/Max`, `Misc` (`BlueCorrection`, `ExpandGamut`, `SceneColorTint`).
- **Post Process Volume**: `Settings` (`FPostProcessSettings`): `ColorSaturation`, `ColorContrast`, `ColorGamma`, `ColorGain`, `ColorOffset` and the `…Shadows` / `…Midtones` / `…Highlights` variants, `WhiteTemp`, `WhiteTint`, each with `bOverride_*`; plus `bUnbound`, `bEnabled`, `Priority`, `BlendWeight`.
- **Colour Correct Regions** (`Experimental/ColorCorrectRegions`): `AColorCorrectRegion` with `Type` (sphere, box, cylinder, capsule), `Enabled`, `Intensity`, `Inner`, `Outer`, `Falloff`, `Invert`, `TemperatureType`, `Temperature`, `Tint`, `ColorGradingSettings` (`FColorGradingSettings`: Global / Shadows / Midtones / Highlights, no override flags), per-actor CC. `AColorCorrectWindow` has the same grading. The plugin may not be enabled in a project; then the category is shown empty with a plain explanation.

So one grade data model (white balance + four tonal ranges of five Vector4 controls, with optional override flags) covers all four target kinds. Unreal's own Colour Grading panel (`DisplayClusterColorGrading` module, with data model generators for root actor, ICVFX camera, PPV and CCR) covers the same targets and is the reference for how each maps.

## Approaches

Considered:

- **A: Python route (chosen by the owner).** Read and write grades with short Python scripts run inside Unreal through Remote Control (`run_python`, `OPB_RESULT:` marker, `_lit` literals), the same route as quick controls. Works in -game nodes, can use editor transactions for undo, and reaches nested arrays (per-viewport / per-node entries) easily. About 20 ms per send.
- **B: Direct Remote Control property writes.** Slightly faster per send, but 5.8 blocks many calls without the same unlock anyway, nested array paths have broken between versions, and it would be a second route to maintain.
- **C: Remote Control Presets.** Ruled out: needs a preset built by hand in every project.

### Delivery during a drag

Normally editor changes reach the wall only through multi-user. During a drag, each change is sent **straight to every running nDisplay node and to the editor** (plain set, no transaction), so the wall updates live even when the session is slow or absent; on release the editor records the final value once inside a `ScopedEditorTransaction` (so multi-user carries it and Undo in the editor works). Values are absolute, so the end state is the same everywhere. With no editor running, changes go to every node only (`editor_or_nodes(all_nodes=True)` pattern), skipping the transaction in a game world. *(Proposed to the owner; the probes must confirm the direct node sends don't fight Concert. Fallback: see Behaviour rules.)*

## Design

### Data model (core, no Qt)

- `Grade`: white balance (temp, tint), four ranges (global, shadows, midtones, highlights) × five controls (saturation, contrast, gamma, gain, offset) as `(r, g, b, y)`, range limits, and the set of override flags in use. Serialises to plain JSON.
- `GradeTarget`: kind (`ndisplay_cluster`, `ndisplay_viewport`, `icvfx_all_nodes`, `icvfx_node`, `ppv`, `ccr`), actor reference (label/path as in quick controls), entry index + names for array entries, display name.
- `Look`: name, `{target key: Grade}`, saved in the project folder (`looks.json`, own format id, travels in `.opproject` exports and templates like `custom_pages.json`).
- Bypass: per target, switch off the target's grading switch (see Behaviour rules); state saved to disk.

### Remote layer

- `scripts.py` gains: list grading targets (all four kinds, priority order), read a target's grade, write part of a grade (one control or a whole grade), bypass on/off. Same `_lit`/`OPB_RESULT:` conventions.
- A `GradingService` (like `ObjectService`) and a background **latest-value sender** (Takes panel pattern) that coalesces drag updates per target+control.

### UI

- `ui/pages/grading.py` (page), a custom-painted `ColorWheel` widget in `ui/widgets.py` or its own module, a wheel group widget (title, italic Unreal subtitle, wheel, brightness slider, numeric fields with RGB/Hex cycle).
- Layout sketch:

```
 Colour Grading                                   [Bypass] [Reset all]
 Target: [ nDisplay · Entire cluster        ▾ ]     Looks: [Day ext warm ▾] [Save…]
 ┌Primaries | Advanced: tonal ranges ─────────────────────────────────────────┐
 │   Lift         Gamma         Gain          Offset                          │
 │  Shadows·Offset Global·Gamma Global·Gain   Global·Offset                   │
 │   ( wheel )    ( wheel )    ( wheel )     ( wheel )                        │
 │   ───●───      ───●───      ───●───       ───●───                          │
 │  0.00 0.00 0.00 [RGB]  …                                                   │
 │   Saturation   Contrast      Temperature  ──────●──── 6500 K               │
 │   ( wheel )    ( wheel )     Tint         ────●────── 0.00                 │
 └────────────────────────────────────────────────────────────────────────────┘
```

- Look and feel: dark, Live FX-like wheels; detailed styling decided under the frontend-design pass *(builder's call within the existing app theme)*.

### Wiring

- Actions in `context.py`: `grading.look`, `grading.bypass`, `grading.reset`, `grading.nudge` (with editors in `ui/action_editors.py`).
- Change log entries via `ctx.changes.record` per gesture; `ctx.apply_setting` learns kind `grade` for Undo.
- Saved settings presets: new item type `grading_look` (applied after `object_settings`, before `cameras`) *(builder's call on exact position)*.
- Take log: each take records the active look name (or "unsaved grade").
- Default surface layout: a Grading page.
- `docs/ROADMAP.md`: MIDI knobs/faders for grading.

### Behaviour rules (settled after the builder check)

Owner's decisions:

- **Partial failures**: when a send or a look recall reaches some nodes/targets but not others, keep going and show a clear red warning naming what missed it; re-send the latest value to a missed node once it answers again. No rollback. A drag is never stopped.
- **Look matching**: targets are matched by actor label; per-viewport / per-node entries by the viewport or node names they apply to (`ApplyPostProcessToObjects`), not by index. Looks are not tied to a level. On recall, missing targets are skipped with a warning; inside a saved settings preset the usual `PresetProblem` retry / skip / cancel prompt appears.
- **Overrides and enable switches**: touching a control ticks that setting's `bOverride_*` automatically. If the whole grading entry or master switch is off (`EnableColorGrading`, an entry's `bIsEnabled`, `EnableInnerFrustumColorGrading`, a PPV's `bEnabled`, a CCR's `Enabled`), the controls show greyed with one "Enable grading on this target" button; nothing is switched on silently.
- **Reset** sets the neutral value **and unticks the override**, so the target falls back to what Unreal would do untouched (lower-priority PPVs blend in again). A look therefore stores override state as well as values.
- **Bypass** turns off the target's grading switch only (no values change), so un-bypassing restores exactly. While bypassed, touching a wheel, recalling a look or nudging ends the bypass first. Bypass state is saved to disk; if Operator Board closes while something is bypassed, it offers to restore it at the next start.

Builder's calls (made during the check; owner may correct):

- **Selected target is shared app-wide** (held in `GradingService`, saved in the project), used by the page, custom-page panels and Stream Deck nudge / bypass / reset. With nothing chosen yet it defaults to the nDisplay Entire Cluster grade (first target in priority order).
- **Fallback if direct node sends fight multi-user**: send to the nodes only during a drag and write the editor once on release.
- **Changes made elsewhere**: the page re-reads a target's grade when it is selected, every few seconds while the page (or a custom page showing grading controls) is live, and just before a gesture starts, so a drag never starts from a stale value. The change log's "previous value" is the value read at gesture start.

## Testing

- Unit tests for the grade model, look storage, hex↔RGB tint conversion, nudge maths, script generation (fake Remote Control listener), the latest-value sender, presets step and change log entries.
- Offscreen Qt tests for the page and wheel (mouse drag, Shift, double-click, typing, RGB/Hex cycle), and Custom Pages offering each control.
- Real-window try-out on the demo stage (`docs/make_screenshots.demo_context()`), with a simulated grading backend.
- Live probes and end-to-end checks in throwaway UE 5.8, then 5.7 and 5.6 editors and -game nodes under the port rules.

## Left to the builder

- Exact Python access paths per version (after probes), which switch Bypass uses per target kind (PPV and CCR: their enabled flag), wheel maths (puck position ↔ RGB offset around neutral, ranges per control), slider ranges, send rate tuning, file/module names, styling details, preset item position, which nudges go on the default Grading Stream Deck page.
