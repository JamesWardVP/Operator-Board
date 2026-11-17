# Colour grading: fixes for a real multi-node stage (short design)

Approved by the owner on 2026-11-17. It follows the final review of `2026-11-17-colour-grading-design.md`.

## Intent
Make colour grading behave well on a real stage with several nDisplay nodes:
- a change reaches every wall section at once;
- a node that is down doesn't slow every press;
- a change reported as failed never turns up later;
- every bypass is on record;
- page buttons never overtake a drag.

## Goals (all settled with the owner, Q1-Q9)
1. **Send to everything at once.** Every change, whether a live drag step or a one-off, goes to the editor and to every node at the same time. A delivery returns once all of them have answered. Only the editor gets the editor transaction, as before.
2. **Skip nodes known to be down** on every change, not only during drags. The first miss still costs one timeout. After that, the retrier sends the newest value when the node answers, and the red warning still names it.
3. **Drop retries for a change nothing took.** When a change fails outright, the node retries it queued are dropped. A partial success (at least one instance took it) keeps its retries.
4. **Bypass on, Bypass off, Enable grading and Restore all are in the change log.** Each target gets one entry, with its result: ok, partial or failed. The page's Undo still walks back grade values only.
5. **A wheel greyed mid-drag ends its gesture** at that moment. The last value sent stays, and the gesture is logged as one entry.
6. **One broken nDisplay root** is skipped. Every other target is still listed, and the nDisplay group's note names the root and says why it couldn't be read.
7. **Page buttons wait their turn.** Reset grade, Bypass, Undo, Enable grading, Restore all and the looks actions (recall, save, delete) run on the page's in-order worker, behind any gesture still being sent.
8. **Warnings name the device** as the Devices page shows it, and fall back to the address only when no device matches.
9. **Small robustness fixes:**
   - A nudge amount that is not a number gives "The nudge amount must be a number".
   - A broken look inside a saved settings preset raises the usual preset problem, with retry, skip and cancel.
   - Restoring bypasses tries every target, then names the ones that failed.

## Non-goals
- Rebasing a drag on the value read when it starts. This is logged in phase 5 of `FUTURE_UPDATES.md`.
- The other logged minors.

## Anti-goals
- A change shows on some wall sections well before others.
- The wall changes when the operator was told the change failed.

## Constraints
- Python 3.9. No Qt in `remote/` or `core/`.
- The suite stays green.

## Approaches
- **Chosen: a shared pool of up to 16 sending threads inside `GradingService`.** `_deliver` hands each instance's call to the pool and waits for all of them. It keeps today's one-call shape, so every caller and rule stays the same.
- **Not chosen: one permanent sender thread per node.** It is faster for drags, but it needs a second, separate path for one-off changes and transactions, so there is more to get wrong.

## Decided how
- **Nodes and transactions.** Nodes no longer wait for the editor's result to decide whether to run a transaction. They run one only when no editor is configured. On `-game` nodes the script skips transactions anyway, so behaviour on the wall is unchanged.
- **Device names.** Each client knows its device name. `targets.py` sets it when the client is made.
- **Change log kind.** Bypass entries use their own kind, `grade_switch`. The page's Undo walks `grade` records only.

## Left to the builder
- The pool's size (up to 16), and its lifetime (made once, closed by `stop()`).
- The exact wording of the root-skipped note.
