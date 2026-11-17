# Colour Grading Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Live FX-style Colour Grading page that grades the live Unreal image (nDisplay cluster/viewports, ICVFX camera, Post Process Volumes, Colour Correct Regions) with wheels, looks, bypass, reset, undo and Stream Deck nudges.

**Architecture:** A Qt-free core grade model (`core/grading.py`) and look store (`core/looks.py`); Python scripts run inside Unreal through Remote Control (`remote/scripts.py`, same route as quick controls); a `GradingService` (`remote/grading.py`) that owns the shared selection, a latest-value background sender, delivery to editor + nodes, bypass and looks; actions in `context.py`; a custom-painted wheel widget and the page in `ui/`.

**Tech Stack:** Python 3.9+, PySide6 (PySide2 fallback via `operator_board/qt.py`), Unreal Remote Control HTTP + Python Editor Script Plugin, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-colour-grading-design.md` (read it with this plan).

## Global Constraints

- Python 3.9 compatible: no `match`, no runtime `X | Y`, no walrus.
- Core packages (`core/`, `remote/`, `surfaces/` non-UI) must not import Qt; only `ui/` and `qt.py` do.
- Add features as actions in the single `ActionRegistry` (`context.py`).
- Pages use `self.is_live()`, never `self.isVisible()`; controls offered to Custom Pages are widget attributes inside titled `Card`s.
- Unreal probes: Remote Control ports 30050+, check free with `netstat -ano`, confirm the answering PID is the probe's own before any change; never the owner's live stage.
- Run tests as `.venv/Scripts/python.exe -m pytest -q > <scratchpad>/pytest.txt 2>&1; echo $?` then tail the file (never pipe pytest).
- Write multi-line patch scripts to the scratchpad with the Write tool; no Bash heredocs with triple quotes.
- README lists shipped features only; ideas go in `docs/ROADMAP.md`; no model identifiers in docs or commits; anything bundled must be open source.
- Commit on `Development` locally as tasks finish; **push and open a PR only when the owner asks**. The owner merges.
- Default nudge amounts (verbatim from spec): Temperature ±100 K, Tint ±0.05, Lift / Gamma / Gain / Offset brightness ±0.02, Saturation and Contrast ±5 %.
- Send rate during drags: about 20 per second, latest value wins; one change log entry per gesture.

## Review Focus

1. **A target whose actor was deleted or renamed while selected** → page shows "Target no longer in the level", nudges report failure, no crash. (Test in Task 5.)
2. **One nDisplay node unreachable mid-drag** → drag continues, red warning names the node, the latest value is re-sent when it answers. (Test in Task 5.)
3. **Typed numbers out of range or malformed in the RGB / Hex fields** (`#GGG`, `abc`, `-5` for gamma) → field reverts or clamps to the control's range, nothing is sent. (Test in Task 7.)
4. **Recalling a look while a target is bypassed** → bypass ends first, then the look applies; bypass state file is cleared for that target. (Test in Task 5.)
5. **Project with the Colour Correct Regions plugin disabled, or no nDisplay root** → those categories show empty with a plain explanation; other targets still work. (Test in Task 4 for the script result shape, Task 8 for the page.)

---

### Task 1: Probe Unreal 5.8 (read-only) and record the real access paths

**Files:**
- Create (scratchpad, not committed): `<scratchpad>/grading_probe.py`
- Modify: `CLAUDE.md` (new "Colour grading" block under "Key decisions & research findings")

**Interfaces:**
- Produces: confirmed Python access paths, per target kind, for: listing targets; reading/writing `ColorGradingSettings` (nDisplay root `StageSettings`, ICVFX camera `CameraSettings`), `PostProcessVolume.settings`, `ColorCorrectRegion` / `ColorCorrectWindow`; struct-copy-modify-write behaviour (does `get_editor_property` return a copy that must be written back?); whether `Name` is absent on -game; enable switches used for Bypass. Tasks 4 and 10 depend on this.

- [ ] **Step 1:** Write a read-only probe script that, for the open level, prints as JSON: each `DisplayClusterRootActor` and how to reach its config data and `stage_settings` (try `get_editor_property('current_config_data')`, `get_config_data()`), the `entire_cluster_color_grading` and `per_viewport_color_grading` contents; each `DisplayClusterICVFXCameraComponent` and its `camera_settings` grading fields; each `PostProcessVolume` `settings.color_gain` etc. with `b_override_*`; each `ColorCorrectRegion` / `ColorCorrectWindow` grading fields and `type`. No setters.
- [ ] **Step 2:** Find a free port: `netstat -ano | findstr :3005` shows nothing for 30050/30060. Launch UE 5.8 editor on a throwaway copy of the nDisplay test project with `-ini:RemoteControl:[/Script/RemoteControlCommon.RemoteControlSettings]:RemoteControlHttpServerPort=30050` plus the `launcher.REMOTE_CONTROL_UNLOCK` flags. Confirm the PID listening on 30050 is the one just launched.
- [ ] **Step 3:** Run the probe through `RemoteControlClient("127.0.0.1", 30050).run_python(python_result_wrapper(...))`. Expected: JSON describing each target kind. Add a PPV and a CCR (sphere) to the throwaway level if absent (throwaway project only).
- [ ] **Step 4:** Repeat on a `-game` node of the same throwaway project (port 30060) to confirm what is readable without the editor.
- [ ] **Step 5:** Write the findings (paths, copy-write behaviour, editor-only fields, enable switches) into `CLAUDE.md`. Close the probe instances.
- [ ] **Step 6:** Commit: `git add CLAUDE.md && git commit -m "docs: record Unreal 5.8 colour grading access paths"`

---

### Task 2: Grade model, controls catalogue, hex tint and nudge maths

**Files:**
- Create: `operator_board/core/grading.py`
- Test: `tests/test_grading_model.py`

**Interfaces:**
- Produces:
  - `RANGES = ("global", "shadows", "midtones", "highlights")`, `PARTS = ("saturation", "contrast", "gamma", "gain", "offset")`
  - `NEUTRAL = {"saturation": (1,1,1,1), "contrast": (1,1,1,1), "gamma": (1,1,1,1), "gain": (1,1,1,1), "offset": (0,0,0,0)}`, `NEUTRAL_TEMP = 6500.0`, `NEUTRAL_TINT = 0.0`
  - `@dataclass Control: id: str, title: str, subtitle: str, kind: str ("wheel"|"slider"), range: str, part: str, lo: float, hi: float, nudge: float`
  - `PRIMARIES: List[Control]` in display order: lift (`shadows`/`offset`, subtitle "Shadows · Offset"), gamma, gain, offset (global), saturation, contrast (global), temperature (subtitle "White Balance · Temp", 1500–15000, nudge 100), tint (subtitle "White Balance · Tint", -1–1, nudge 0.05). Wheel brightness nudge 0.02; saturation/contrast nudge 0.05 (= 5 %).
  - `ADVANCED: List[Control]` for shadows/midtones/highlights × PARTS (ids like `shadows.gain`).
  - `CONTROLS: Dict[str, Control]` (all, by id)
  - `@dataclass Grade: values: Dict[str, Tuple[float,float,float,float]]` keyed `"<range>.<part>"`, `temp: float`, `tint: float`, `overrides: Set[str]` (keys whose override is ticked, plus `"temp"`/`"tint"`), `limits: Dict[str, float]` (`shadows_max`, `highlights_min`, `highlights_max`); `to_dict()`, `Grade.from_dict(d)`, `neutral()` classmethod, `get(key)`, `set(key, value) -> Grade` (ticks the override), `reset(key) -> Grade` (neutral value, override unticked)
  - `tint_hex(rgb: Tuple[float,float,float]) -> str` and `hex_to_tint(hex: str, y: float) -> Tuple[float,float,float,float]` (hue/strength only; brightness `y` kept); raises `ValueError` on malformed hex
  - `nudge(grade: Grade, control_id: str, amount: float, channel: str = "y") -> Grade` (`channel` in `r|g|b|y`; result clamped to the control's `lo..hi`)

- [ ] **Step 1: Write the failing tests**

```python
def test_neutral_grade_has_no_overrides():
    g = Grade.neutral()
    assert g.get("global.gain") == (1, 1, 1, 1) and g.get("global.offset") == (0, 0, 0, 0)
    assert g.overrides == set()

def test_set_ticks_override_and_reset_unticks():
    g = Grade.neutral().set("global.gain", (1.1, 1.0, 0.9, 1.0))
    assert "global.gain" in g.overrides
    g = g.reset("global.gain")
    assert g.get("global.gain") == (1, 1, 1, 1) and "global.gain" not in g.overrides

def test_lift_maps_to_shadows_offset():
    c = CONTROLS["lift"]
    assert (c.range, c.part, c.subtitle) == ("shadows", "offset", "Shadows · Offset")

def test_round_trip_dict():
    g = Grade.neutral().set("shadows.gamma", (0.9, 1, 1, 1.05))
    assert Grade.from_dict(g.to_dict()) == g

def test_hex_shows_tint_without_brightness():
    assert tint_hex((1.0, 1.0, 1.0)) == "#FFFFFF"
    assert hex_to_tint("#FFFFFF", 1.2)[3] == 1.2
    with pytest.raises(ValueError):
        hex_to_tint("#GGG", 1.0)

def test_nudge_defaults_and_clamp():
    g = nudge(Grade.neutral(), "temperature", +CONTROLS["temperature"].nudge)
    assert g.temp == 6600.0 and "temp" in g.overrides
    g = nudge(Grade.neutral(), "gain", +0.02)
    assert g.get("global.gain")[3] == pytest.approx(1.02)
    assert nudge(Grade.neutral(), "tint", +5).tint == 1.0  # clamped
```

- [ ] **Step 2:** Run `.venv/Scripts/python.exe -m pytest tests/test_grading_model.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `operator_board/core/grading.py`. Grade is immutable-style (methods return a new Grade). Tint hex: normalise the RGB so its maximum channel is 1, then map to 0–255; `hex_to_tint` scales the parsed colour so its luminance-neutral mean matches the wheel's current colour strength convention (`rgb / max(rgb)`), and returns `(r, g, b, y)`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit `feat: colour grade model and controls`.

---

### Task 3: Looks store, target keys and bypass state file

**Files:**
- Create: `operator_board/core/looks.py`
- Modify: `operator_board/core/projects.py` (document `looks.json` in the folder list; it travels in exports and templates, and `create(setup_from=...)` copies it, like `custom_pages.json`)
- Test: `tests/test_looks.py`

**Interfaces:**
- Consumes: `Grade` (Task 2).
- Produces:
  - `FILE_NAME = "looks.json"`, `FILE_FORMAT = "operator-board-looks"`, `BYPASS_FILE = "grading_bypass.json"`
  - `target_key(kind: str, actor: str, applies_to: Sequence[str] = ()) -> str` → e.g. `"ndisplay_cluster|nDisplay_Root"`, `"ndisplay_viewport|nDisplay_Root|VP_1,VP_2"` (names sorted), matched by label and applied-to names, never by index
  - `@dataclass Look: name: str, grades: Dict[str, Grade]`
  - `class LookStore(path, bus)`: `names() -> List[str]`, `get(name) -> Optional[Look]`, `save(look)` (replace by name, publishes `grading.looks_changed`), `delete(name)`, `export(path)`, `import_file(path) -> List[str]`
  - `class BypassState(path)`: `set(key, bypassed: bool)`, `bypassed() -> List[str]`, `clear()`

- [ ] **Step 1: Write the failing tests**: `test_target_key_ignores_viewport_order` (`target_key("ndisplay_viewport","Root",["B","A"]) == target_key("ndisplay_viewport","Root",["A","B"])`), `test_look_save_get_round_trip`, `test_save_same_name_replaces`, `test_file_has_format_id`, `test_import_rejects_other_format` (raises `ValueError`), `test_bypass_state_survives_reload` (set two keys, new `BypassState(same path).bypassed()` returns both), `test_project_export_includes_looks` (in `tests/test_projects.py`: a project with `looks.json` exports it, a template keeps it).
- [ ] **Step 2:** Run `pytest tests/test_looks.py tests/test_projects.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement with `atomic_write` and `serde` as in `core/custom_pages.py`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit `feat: grading looks and bypass state storage`.

---

### Task 4: Grading scripts that run inside Unreal

**Files:**
- Modify: `operator_board/remote/scripts.py` (new section after the quick controls)
- Test: `tests/test_remote.py` (add to the compile-every-script test) and `tests/test_grading_scripts.py`

**Interfaces:**
- Consumes: access paths from Task 1 (in `CLAUDE.md`); `_lit`, `FIND_ACTOR`, `EDITOR_WORLD`.
- Produces (each returns a script body for `python_result_wrapper`):
  - `list_grading_targets() -> str` → result `{"targets": [{"key", "kind", "actor", "applies_to": [...], "name", "enabled": bool}], "missing": {"ccr": "plugin not enabled" | "", "ndisplay": "" | "no nDisplay root"}}` in priority order: ndisplay_cluster, ndisplay_viewport…, icvfx_all_nodes, icvfx_node…, ppv…, ccr…
  - `read_grade(kind: str, actor: str, applies_to: List[str]) -> str` → result `{"grade": Grade.to_dict(), "enabled": bool}` or `{"error": "Target no longer in the level"}`
  - `write_grade(kind, actor, applies_to, values: Dict[str, Any], transact: bool) -> str` → writes only the given keys (`"<range>.<part>"`, `"temp"`, `"tint"`), ticking their override; key with value `None` = reset (neutral + untick); `transact=True` wraps in `ScopedEditorTransaction` only when an editor world exists
  - `set_grading_enabled(kind, actor, applies_to, enabled: bool, transact: bool) -> str` (Bypass / "Enable grading on this target": the entry's or actor's own switch)

- [ ] **Step 1: Write the failing tests**: every new script compiles after `python_result_wrapper` (extend the existing loop in `tests/test_remote.py`, including names with apostrophes like `"Cam's PPV"`); `test_write_grade_only_mentions_given_keys` (script for `{"global.gain": ...}` doesn't contain `color_gamma`); `test_write_grade_transaction_flag` (`transact=False` script has no `ScopedEditorTransaction`). Then an execution test with a fake `unreal` module (`tests/fake_unreal.py`: classes for root actor, ICVFX camera component, PPV, CCR with the struct shapes recorded in Task 1) that runs `list_grading_targets`, `read_grade`, `write_grade` and checks: priority order; a missing CCR plugin returns `missing["ccr"]`; reset unticks the override; a deleted actor returns the error.
- [ ] **Step 2:** Run `pytest tests/test_grading_scripts.py tests/test_remote.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. Struct values are copied, modified and written back with `set_editor_property` (or as Task 1 found). Vector4 ↔ `unreal.Vector4(x, y, z, w)`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit `feat: Unreal scripts for colour grading targets`.

---

### Task 5: GradingService: selection, live sender, delivery, bypass, looks, nudge

**Files:**
- Create: `operator_board/remote/grading.py`
- Test: `tests/test_grading_service.py`

**Interfaces:**
- Consumes: Tasks 2–4; `TargetResolver.editor_or_nodes`, `resolve(TARGET_NDISPLAY)`, `primary()`; `EventBus`.
- Produces `class GradingService(config, targets, bus, looks: LookStore, bypass: BypassState)`:
  - `refresh_targets() -> List[Dict]` (reads via `editor_or_nodes`), `targets_list` attribute, `missing` attribute
  - `selected` (target key; default first in priority order), `select(key)` (saved in `config.grading.selected`, publishes `grading.selected`)
  - `read(key) -> Grade` (also caches `self.current[key]`)
  - `begin_gesture(key) -> Grade` (re-reads, remembers the value for the change log)
  - `send(key, values: Dict[str, Any])` (non-blocking; coalesces per key, latest wins; delivers to the editor without transaction **and** every running nDisplay node; no editor → every node)
  - `end_gesture(key, label: str)` (flushes, writes the final values once to the editor with `transact=True`, records one change log entry via a `record` callback with `kind="grade"`, `ref={"key": key}`, value/previous = grade dicts)
  - `reset(key, control_id: Optional[str] = None)` (one control or whole grade)
  - `set_bypass(key, on: Optional[bool]) -> bool` (None = toggle; saved in `BypassState`); `is_bypassed(key)`
  - `recall_look(name) -> List[str]` (warnings for skipped targets; ends bypass on each target first)
  - `save_look(name, keys: List[str])`
  - `nudge(control_id, amount, key: str = "", channel: str = "y")` (`key=""` → `selected`; ends bypass first)
  - `apply(key, grade_dict)` (used by Undo and presets; raises `RuntimeError` on failure)
  - Events: `grading.changed` (key, grade), `grading.problem` (message naming missed hosts), `grading.targets`
  - Missed nodes: remembered per key; a 2-second retry thread re-sends the latest values to them until they answer.
- Config: add `@dataclass GradingSettings: selected: str = ""` to `core/config.py` as `AppConfig.grading` (round-trips through `serde`).

- [ ] **Step 1: Write the failing tests** using a fake target resolver whose clients record `run_python` calls (no HTTP):
  - `test_send_coalesces_latest_value` (100 quick `send` calls for one key → far fewer script runs, last one carries the last value)
  - `test_drag_goes_to_editor_and_nodes` (editor + 2 nodes each get the script; only the final editor write has `ScopedEditorTransaction`)
  - `test_no_editor_goes_to_every_node`
  - `test_one_gesture_one_change_log_entry` (begin, 30 sends, end → `record` called once with previous = value at begin)
  - `test_unreachable_node_warns_and_resends` (node raises `RemoteControlError("unreachable")` → `grading.problem` published naming it; when it starts answering, it receives the latest values)
  - `test_bypass_toggle_saved_and_touch_ends_it` (bypass on → `BypassState.bypassed()` has key; `nudge` on that key sends enable=True first)
  - `test_recall_look_skips_missing_target_with_warning`
  - `test_recall_look_while_bypassed_ends_bypass_first`
  - `test_selected_target_deleted` (read returns error → `grading.problem` "Target no longer in the level", no exception escapes `nudge`)
  - `test_nudge_without_selection_uses_first_target`
- [ ] **Step 2:** Run `pytest tests/test_grading_service.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. The sender is one daemon thread per service (Takes `_sync_worker` pattern), pacing to at most one send per key every 50 ms.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit `feat: grading service with live sender, bypass and looks`.

---

### Task 6: Wire into AppContext: actions, Undo, change log, takes, presets, default Grading surface page

**Files:**
- Modify: `operator_board/context.py`, `operator_board/core/presets.py`, `operator_board/remote/takes.py`, `operator_board/surfaces/layout.py`, `operator_board/ui/action_editors.py` (nudge parameter editor: control choice, amount, target choice incl. "Selected on the Colour Grading page"), `operator_board/app.py` or `ui/main_window.py` (startup bypass-restore prompt), `docs/ROADMAP.md` (MIDI knobs and faders for grading)
- Test: `tests/test_grading_context.py`, extend `tests/test_led_modes_presets.py` pattern for the preset item

**Interfaces:**
- Consumes: `GradingService` (Task 5).
- Produces:
  - `ctx.grading: GradingService`, `ctx.looks: LookStore` (in the project folder)
  - Actions (category "Colour Grading"): `grading.look` (param `look`, choices = `looks.names`), `grading.bypass` (params `target` incl. `""` = selected, `state` choice toggle/on/off; `state=` callback shows bypass lit), `grading.reset` (param `target`, `""` selected, `"all"`), `grading.nudge` (params `control` choice from `CONTROLS`, `amount` float defaulting to the control's nudge, `channel` r/g/b/y, `target`)
  - `ctx.apply_setting("grade", {"key": k}, grade_dict)` → `grading.apply`
  - Preset item type `"grading_look"` ("Colour grading look", "Recall the saved look on every target it holds"), placed in `ITEM_TYPES` after `object_settings`; missing targets raise `PresetProblem` naming them
  - `TakeRecord.look: str = ""` (active look name or "unsaved grade"), set at take start; added to the production export Takes sheet
  - `default_layout()` gains a "Grading" page: Bypass, Reset, Temperature −/+, Tint −/+, Gain −/+, Saturation −/+, and recall of the first look
  - Startup: when `BypassState.bypassed()` is not empty, ask "Grading was bypassed on N targets when Operator Board closed. Restore the grade?" (Restore / Leave bypassed)

- [ ] **Step 1: Write the failing tests**: each action exists with its params; invoking `grading.nudge` with defaults calls `grading.nudge("gain", 0.02, "", "y")`; `apply_setting("grade", ...)` routes to `grading.apply`; a preset with `grading_look` runs after `object_settings` and raises `PresetProblem` on a missing target; a take started with an active look records `look`; `default_layout()` has a page named "Grading" whose actions all exist in the registry.
- [ ] **Step 2:** Run `pytest tests/test_grading_context.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the whole suite (Global Constraints command). Expected: all pass (207 previous + new).
- [ ] **Step 5:** Commit `feat: colour grading actions, presets, takes and surface page`.

---

### Task 7: Colour wheel widget

Invoke `frontend-design` before this task (desktop Qt, but it is new visual UI); the look follows the existing dark app theme with Live FX-style wheels.

**Files:**
- Create: `operator_board/ui/color_wheel.py`
- Test: `tests/test_color_wheel.py` (offscreen Qt)

**Interfaces:**
- Consumes: `Control`, `tint_hex`, `hex_to_tint` (Task 2).
- Produces:
  - `class ColorWheel(QWidget)`: signals `changed(tuple)` (r, g, b, y during a drag), `gestureStarted()`, `gestureFinished()`; `set_value(tuple)`; drag the puck = colour, Shift = fine (×0.1), double-click = reset (emits `gestureStarted`, `changed(neutral)`, `gestureFinished`), wheel scroll = brightness.
  - `class WheelControl(QWidget)`: title label, italic subtitle label, `ColorWheel`, brightness slider, value fields, cycle button (`RGB` ↔ `Hex`); attributes `wheel`, `brightness`, `fields`, `mode_button`; same signals forwarded.
  - `class GradeSlider(QWidget)` for Temperature / Tint: title, italic subtitle, slider, typed value.

- [ ] **Step 1: Write the failing tests** with `QTest` mouse events: dragging from centre right emits `changed` with r > g, b and the same y; Shift-drag of the same distance moves 10× less; double-click emits neutral and both gesture signals; the cycle button switches the fields to one hex field showing `tint_hex`; typing `#GGG` or `abc` leaves the value unchanged and emits nothing; typing `-5` in a gamma field clamps to the control's `lo`.
- [ ] **Step 2:** Run `pytest tests/test_color_wheel.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement with `QPainter` (conical hue gradient + radial saturation fade, puck, crosshair, neutral marker).
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit `feat: colour wheel and grading slider widgets`.

---

### Task 8: Colour Grading page, side menu and Custom Pages

**Files:**
- Create: `operator_board/ui/pages/grading.py`
- Modify: `operator_board/ui/main_window.py` (`PAGES`, `NAV`: straight after Remote Objects)
- Test: `tests/test_grading_page.py`, extend `tests/test_custom_pages.py`

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces `class GradingPage(BasePage)`, title "Colour Grading", with titled `Card`s: "Target" (`target_picker` combo grouped in priority order with headers; `enable_button` "Enable grading on this target"), "Looks" (`looks_picker`, `save_look_button` → dialog with name and a tick per target, `delete_look_button`), "Primaries" (attributes `lift`, `gamma`, `gain`, `offset`, `saturation`, `contrast`, `temperature`, `tint`), "Advanced: tonal ranges" (tab; attributes per range), toolbar `bypass_button`, `reset_button`; `control_labels` for plain names; empty-category messages from `grading.missing`.

- [ ] **Step 1: Write the failing tests**: page is in the side menu right after "Remote Objects"; selecting a target calls `grading.select` and fills the wheels from `read`; a wheel drag calls `begin_gesture`, several `send`, `end_gesture`; greyed controls + visible `enable_button` when the target's `enabled` is false; `grading.problem` shows a red banner; "CCR plugin not enabled" message shows when `missing["ccr"]` is set; Custom Pages offers `ctl:gain`, `ctl:temperature`, `ctl:target_picker`, `ctl:looks_picker`, `ctl:bypass_button` and the "Primaries" card; page uses `is_live()` for its periodic re-read (every 3 s).
- [ ] **Step 2:** Run `pytest tests/test_grading_page.py tests/test_custom_pages.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the whole suite. Expected: all pass.
- [ ] **Step 5:** Try it in a real window: add a simulated grading backend to `docs/make_screenshots.demo_context()` (fake targets of all four kinds), open the page, drag wheels with `QTest`, save `win.grab()` to the scratchpad and look at it. Fix what looks wrong.
- [ ] **Step 6:** Commit `feat: Colour Grading page`.

---

### Task 9: Live verification on UE 5.8, 5.7 and 5.6; source check for 5.2–5.5

**Files:**
- Modify: `CLAUDE.md`, `operator_board/remote/scripts.py` (only if a version differs)

- [ ] **Step 1:** Under the port rules (30050+, free, own PID), start a throwaway 5.8 editor and a `-game` node of the throwaway nDisplay project. Run Operator Board against them (`--config` pointing at a throwaway config). For each target kind: select, drag Gain, nudge Temperature, Bypass on/off, Reset, save and recall a look, Undo. Confirm in the editor's details panel that the values match, and that the node shows the same values (read back with `read_grade`).
- [ ] **Step 2:** Check whether direct node sends fight multi-user: with a throwaway multi-user server and session, drag for 5 s, release, read back on editor and node; values must match the last drag value. If they don't, switch to the spec's fallback (nodes only during the drag, editor once on release) and re-test.
- [ ] **Step 3:** Repeat Step 1 on 5.7 and 5.6.
- [ ] **Step 4:** For 5.2–5.5, compare the nDisplay / PPV / CCR grading headers against Epic's published source for those versions (GitHub EpicGames/UnrealEngine tags need the owner's linked account; otherwise use the documentation pages). Record differences; mark 5.2–5.5 grading "unverified" in `CLAUDE.md` "Not yet verified".
- [ ] **Step 5:** Run the whole suite. Commit `fix/docs: colour grading verified on 5.6-5.8`.

---

### Task 10: Docs, reviews and handover

**Files:**
- Modify: `README.md` (Colour Grading under shipped features), `CLAUDE.md` (current state, a "Colour grading" decisions block, remove "don't start" note), `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `../FUTURE_UPDATES.md` (section 3 marked built), memory `future-updates-priority.md`

- [ ] **Step 1:** Update the docs listed above.
- [ ] **Step 2:** Run `design:accessibility-review` on the page (keyboard access to wheels, contrast of italic subtitles, focus rings) and fix findings. (Web-only skills `web-design-guidelines` and `ux-ui-audit` do not apply to this Qt desktop page.)
- [ ] **Step 3:** `superpowers:verification-before-completion`: full test suite, report real results.
- [ ] **Step 4:** `superpowers:requesting-code-review` on the branch; act on findings with `superpowers:receiving-code-review`.
- [ ] **Step 5:** Commit `docs: colour grading page`. Ask the owner whether to push and open the PR to `main`; release 0.9.0 (`bump_version.py 0.9.0`, website brief) only when they say so.
