# Colour Grading Stage Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make colour grading behave well on a real stage with several nDisplay nodes: a change reaches every instance at once, dead nodes cost nothing after their first miss, failed changes never arrive later, bypasses are logged, and page buttons never overtake a drag.

**Architecture:**
- `GradingService._deliver` sends to the editor and the nodes through a small thread pool and waits for all of them.
- Retry bookkeeping and switch logging stay inside the service.
- The page routes every Unreal-touching button through its existing in-order worker (`_Shared.put`).
- The Unreal script skips a root it can't read.

**Tech Stack:** Python 3.9, PySide6, `concurrent.futures`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-colour-grading-fixes-design.md`

## Global Constraints
- Python 3.9 compatible: no `match`, no runtime `X | Y` unions, no walrus.
- `core/` and `remote/` must not import Qt.
- Run pytest as `.venv/Scripts/python.exe -m pytest -q > <file> 2>&1` and read the file. Never pipe it.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't push.
- Keep model names out of commit messages and docs.

## Review Focus
1. **Every instance down at once:** a change reports a plain failure, and nothing is retried later. Covered in Task 2.
2. **A down node during a one-off change:** the call returns after at most one timeout. Two down nodes cost one timeout in total, not two. Covered in Task 1.
3. **A configured device whose address matches no device:** the warning shows the address. Covered in Task 3.
4. **"Export changes" and preset capture never pick up a bypass entry.** They would fail on apply with "Cannot apply a 'grade_switch' setting". Covered in Task 4.
5. **Pressing Reset while a typed value is still being sent:** the wall ends neutral. Covered in Task 7.

---

### Task 1: Send to every instance at once, and skip known-down nodes

**Files:**
- Modify: `operator_board/remote/grading.py` (`__init__`, `stop`, `_deliver`)
- Test: `tests/test_grading_service.py`

**Interfaces:**
- Produces: `GradingService._pool`, a `concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="grading-send")` made in `__init__`. `stop()` shuts it down with `wait=False`. `_deliver(...)` keeps its signature and the `GradingProblem` / `GradingPartial` rules.
- Rules:
  - The editor and the nodes are all submitted to the pool, then all are awaited.
  - A node runs a transaction only when no editor is configured (`primary()` raises `LookupError`).
  - Nodes in `self._missed` are skipped for every change, not only when `live`, and are passed to `_remember_missed(..., quiet=True)` so they get the newest value later.
  - A node that misses for the first time still costs one timeout.

- [ ] **Step 1: Write the failing tests**
  - `test_one_off_change_reaches_nodes_at_the_same_time`:
    - set `delay = 0.3` on two `FakeClient` nodes, and use `on_call` to sleep 0.3 s before the write (for both nodes);
    - time `svc.reset(PPV, "gain")`;
    - assert it took less than 0.55 s and both nodes have writes.
  - `test_known_down_node_is_skipped_on_one_off_changes`:
    - node 0 has `down = True, delay = 0.3`;
    - the first `svc.nudge("gain", 0.02, PPV)` takes about 0.3 s or more;
    - a second nudge takes less than 0.15 s;
    - once node 0 is back up (`down = False`), `wait_for` shows it gets a write.
- [ ] **Step 2: Run them and watch them fail**
  - Run: `.venv/Scripts/python.exe -m pytest -q tests/test_grading_service.py -k "same_time or known_down" > ../t.txt 2>&1`
  - Expected: 2 failed (timing assertions).
- [ ] **Step 3: Implement the pool in `_deliver`, `__init__` and `stop`** as the Interfaces describe.
- [ ] **Step 4: Run `tests/test_grading_service.py`.** Expected: all pass.
- [ ] **Step 5: Commit** with message `fix: grading sends to the editor and every node at once`.

### Task 2: A change nothing took is never retried

**Files:**
- Modify: `operator_board/remote/grading.py` (`_deliver`)
- Test: `tests/test_grading_service.py`

**Interfaces:**
- Consumes: Task 1's `_deliver`.
- Produces: when `took == 0`, `_deliver` removes the `(name, key, what)` entries it added to `self._missed` during this call, then raises `GradingProblem`.

- [ ] **Step 1: Write the failing test** `test_failed_change_is_not_delivered_later`:
  - the editor fails only on writes: `on_call` raises `RemoteControlError("script error")` when `"_g_run(lambda: _g_write(t" in script`, so the read before the nudge still works;
  - every node is `down = True`;
  - `ok, _ = svc.nudge("gain", 0.02, PPV)` gives `not ok`;
  - set every node `down = False`;
  - sleep 0.3 s (`retry_s` is 0.05);
  - assert no node has writes.
- [ ] **Step 2: Run it and watch it fail.** Expected: FAIL (the nodes got the write from the retrier).
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run `tests/test_grading_service.py`.** Expected: all pass.
- [ ] **Step 5: Commit** with message `fix: a grading change nothing took is not sent again later`.

### Task 3: Warnings name the device

**Files:**
- Modify: `operator_board/remote/targets.py` (where clients are made, around line 54)
- Modify: `operator_board/remote/grading.py` (`_client_name`)
- Test: `tests/test_grading_service.py`, `tests/test_targets.py` (or the existing resolver test file)

**Interfaces:**
- Produces: `RemoteControlClient.device_name: str`, set by the resolver to `dev.name` every time it hands out a client. `_client_name(client)` returns `device_name` when it is set, else `host:port`.

- [ ] **Step 1: Write the failing tests**
  - `test_missed_node_warning_names_the_device`: give node 0 `device_name = "render-node-2"` and `down = True`; after a nudge, a `grading.problem` message contains `render-node-2` and does not contain `node0:30010`.
  - A resolver test: a configured `ROLE_NDISPLAY` device named `Node_A` resolves to a client with `device_name == "Node_A"`.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run both test files.** Expected: all pass.
- [ ] **Step 5: Commit** with message `fix: grading warnings name the device`.

### Task 4: Bypass, Enable grading and Restore all go in the change log

**Files:**
- Modify: `operator_board/remote/grading.py` (`set_bypass`, `enable`; new `_log_switch`)
- Modify: `operator_board/context.py` (`changed_settings`)
- Test: `tests/test_grading_service.py`, `tests/test_grading_context.py`

**Interfaces:**
- Produces: `GradingService._log_switch(key: str, setting: str, on: bool, result: str) -> None`.
  - It calls `self.record(CATEGORY, <target name>, setting, value="on" if on else "off", previous="off" if on else "on", result=result, kind="grade_switch", ref={"key": key})`.
  - `setting` is `"Bypass"` or `"Enable grading"`.
  - `result` is `"ok"`, `"partial: <msg>"` or `"failed: <msg>"`.
  - `set_bypass` and `enable` log once per call, including a failure (logged, then raised or returned).
  - `restore_bypassed` logs through `set_bypass`.
  - `changed_settings()` leaves out `kind == "grade_switch"`.

- [ ] **Step 1: Write the failing tests**
  - `test_bypass_and_enable_are_logged`: `set_bypass(PPV, True)`, `set_bypass(PPV, False)` and `enable(PPV)` give three records with `kind == "grade_switch"` and settings `["Bypass", "Bypass", "Enable grading"]`.
  - `test_failed_bypass_is_logged_as_failed`: every instance down; `set_bypass` raises; the last record's result starts with `"failed"`.
  - In `test_grading_context.py`, `test_bypass_entries_are_not_remote_objects_settings`: after `ctx.grading.set_bypass(...)` on the context's fake, `ctx.changed_settings() == []`. Follow `test_grade_changes_are_not_remote_objects_settings`.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run both files.** Expected: all pass. Also check that the page's Undo still filters `kind == "grade"` (`ui/pages/grading.py` `_undoable`); no change is needed there.
- [ ] **Step 5: Commit** with message `fix: bypass and enable grading are in the change log`.

### Task 5: Small robustness fixes

**Files:**
- Modify: `operator_board/context.py` (the `nudge` action)
- Modify: `operator_board/core/presets.py` (`_apply_grading_look`)
- Modify: `operator_board/remote/grading.py` (`restore_bypassed`)
- Test: `tests/test_grading_context.py`, `tests/test_grading_service.py`

**Interfaces:**
- The `nudge` action returns `ActionResult(False, "The nudge amount must be a number")` when `float(amount)` raises `ValueError` or `TypeError`.
- `_apply_grading_look` turns a `TypeError`, `ValueError`, `KeyError` or `AttributeError` from `Look.from_dict` into `PresetProblem("The colour grading look in this preset is damaged")`.
- `restore_bypassed` tries every key. A `GradingProblem` from a key is collected as `"<target label>: <message>"`. It raises one `GradingProblem` that names both the failures and the targets that are gone.

- [ ] **Step 1: Write the failing tests**
  - `test_nudge_action_with_text_amount_is_a_plain_failure`: `ctx.actions.invoke("grading.nudge", control="gain", amount="abc")` is not ok, and its message is exactly `"The nudge amount must be a number"`.
  - `test_broken_look_in_preset_is_a_preset_problem`: applying a `grading_look` item with data `{"name": "x", "grades": 5}` raises `PresetProblem`.
  - `test_restore_bypassed_tries_every_target`:
    - bypass PPV and CLUSTER;
    - make every client fail only for the CLUSTER actor: `on_call` raises `RemoteControlError("Target no longer in the level: Root")` when `'"Root"' in script`. Don't use `gone`, because "Root" also appears in the helper code;
    - `restore_bypassed()` gives `ok is False`, the message names Root, and PPV is no longer bypassed.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run both files.** Expected: all pass.
- [ ] **Step 5: Commit** with message `fix: plain errors for bad nudge amounts and preset looks, restore tries every target`.

### Task 6: One broken nDisplay root doesn't empty the picker

**Files:**
- Modify: `operator_board/remote/scripts.py` (`list_grading_targets`, the `for root in roots:` loop)
- Test: `tests/test_grading_scripts.py`

**Interfaces:**
- The loop wraps each root in `try/except Exception`.
- A root that fails is left out of the camera loop too. Its message is added to `missing['ndisplay']` as `"<label> could not be read: <error>"`, joined with `"; "`.

- [ ] **Step 1: Write the failing test** `test_one_broken_root_keeps_the_other_targets`:
  - in `fu.stage()`, set `level.root.current_config_data = None`;
  - the kinds are `["ppv", "ppv", "ccr"]`;
  - `"nDisplay Root" in out["missing"]["ndisplay"]`.
- [ ] **Step 2: Run it and watch it fail.** Expected: FAIL (an error result, or empty targets).
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run `tests/test_grading_scripts.py`.** Expected: all pass.
- [ ] **Step 5: Commit** with message `fix: a broken nDisplay root no longer hides every grading target`.

### Task 7: Page buttons wait their turn, and a greyed wheel ends its gesture

**Files:**
- Modify: `operator_board/ui/color_wheel.py` (`ColorWheel`, `WheelControl` and `GradeSlider`: a `changeEvent`)
- Modify: `operator_board/ui/pages/grading.py` (new `_queue`; `_bypass_clicked`, `_reset`, `_enable`, `_restore`, `_undo`, the look recall, `_save_look`, `_delete_look`)
- Test: `tests/test_color_wheel.py`, `tests/test_grading_page.py`

**Interfaces:**
- Each control's `changeEvent(event)` calls `self.gesture.finish()` (and, on `ColorWheel`, ends its drag) when `event.type() == QEvent.EnabledChange` and the control is no longer enabled.
- `GradingPage._queue(fn, done, what: str)` runs `fn` through `self._shared.put`. A failure is shown with `self.notify(f"{what}: {exc}", False)`. The buttons listed above use it instead of `self.background`.

- [ ] **Step 1: Write the failing tests**
  - `test_disabling_a_wheel_mid_drag_ends_the_gesture` (`test_color_wheel.py`):
    - press the mouse on a `WheelControl`'s wheel, then move it;
    - `setEnabled(False)`;
    - assert `gestureFinished` fired once and `busy()` is False.
  - `test_reset_waits_for_a_value_still_being_sent` (`test_grading_page.py`):
    - make the fake's `send` sleep 0.3 s;
    - type a Gain value (one-shot), then click `reset_button` at once;
    - `wait_until` the fake's calls show the `reset` after the `send`, and the PPV grade is neutral.
- [ ] **Step 2: Run them and watch them fail.**
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run `tests/test_color_wheel.py`, `tests/test_grading_page.py` and `tests/test_custom_pages.py`.** Expected: all pass.
- [ ] **Step 5: Commit** with message `fix: grading buttons wait for drags, and a greyed wheel ends its drag`.

### Task 8: Docs and the full suite

**Files:**
- Modify: `CLAUDE.md` (the "Colour grading" block: delivery at once, skipped down nodes, dropped retries, `grade_switch` log entries, page buttons on the worker)
- Modify: `../FUTURE_UPDATES.md` (phase 5: none of the eight fixed items should be listed there; confirm)

- [ ] **Step 1: Update the CLAUDE.md block** in plain prose.
- [ ] **Step 2: Run the full suite.** Run: `.venv/Scripts/python.exe -m pytest -q > .superpowers/sdd/2026-11-17-colour-grading-fixes/full.txt 2>&1`. Expected: exit 0, about 405 passed.
- [ ] **Step 3: Commit** with message `docs: colour grading stage fixes`.
