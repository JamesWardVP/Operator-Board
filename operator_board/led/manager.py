"""LED control: one button freezes, blacks out, test-patterns or locates every processor, and each
processor or group of processors can also be set on its own."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple

from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.led.processor import (CAP_BLACKOUT, CAP_FREEZE, CAP_LOCATE, CAP_TEST_PATTERN, MODE_BLACKOUT,
                                          MODE_FROZEN, MODE_LOCATE, MODE_NORMAL, MODE_PATTERN, LEDProcessor)
from operator_board.led.profiles import ProfileRegistry
from operator_board.led.transports import TransportError

log = logging.getLogger(__name__)


class LEDManager:
    def __init__(self, config: AppConfig, bus: EventBus, registry: Optional[ProfileRegistry] = None) -> None:
        self.config = config
        self.bus = bus
        self.registry = registry or ProfileRegistry()
        self.processors: List[LEDProcessor] = []
        self.reload()

    def reload(self) -> None:
        for p in self.processors:
            p.close()
        self.processors = []
        for cfg in self.config.led_processors:
            profile = self.registry.get(cfg.profile_id)
            if profile is None:
                log.warning("LED processor %s uses unknown profile %s", cfg.name, cfg.profile_id)
                continue
            self.processors.append(LEDProcessor(cfg, profile))
        self._publish()

    def active(self) -> List[LEDProcessor]:
        return [p for p in self.processors if p.config.enabled]

    def get(self, name: str) -> Optional[LEDProcessor]:
        return next((p for p in self.processors if p.name == name), None)

    # ---- wall state -------------------------------------------------------------------
    @property
    def frozen(self) -> bool:
        procs = [p for p in self.active() if CAP_FREEZE in p.capabilities()]
        return bool(procs) and all(p.state.frozen for p in procs)

    @property
    def blackout(self) -> bool:
        procs = [p for p in self.active() if CAP_BLACKOUT in p.capabilities()]
        return bool(procs) and all(p.state.blackout for p in procs)

    @property
    def test_pattern(self) -> Optional[str]:
        patterns = {p.state.test_pattern for p in self.active()}
        return patterns.pop() if len(patterns) == 1 else None

    @property
    def locate(self) -> bool:
        procs = [p for p in self.active() if CAP_LOCATE in p.capabilities()]
        return bool(procs) and all(p.state.locate for p in procs)

    def modes(self) -> Dict[str, str]:
        """Each enabled processor's mode (normal, frozen, blackout, pattern, locate)."""
        return {p.name: p.mode for p in self.active()}

    def mode_of(self, names: List[str]) -> Optional[str]:
        """The mode shared by every processor in ``names`` (None when they differ or none are enabled)."""
        modes = {p.mode for p in self.active() if p.name in names}
        return modes.pop() if len(modes) == 1 else None

    def pattern_of(self, names: List[str]) -> Optional[str]:
        patterns = {p.state.test_pattern for p in self.active() if p.name in names}
        return patterns.pop() if len(patterns) == 1 else None

    def resolve(self, target: str) -> List[str]:
        """Processor names for a processor name, a group name, or "" / "all" for every processor."""
        if not target or target == "all":
            return [p.name for p in self.active()]
        group = next((g for g in self.config.led_groups if g.name == target), None)
        if group is not None:
            return [n for n in group.processors if self.get(n)]
        if self.get(target):
            return [target]
        raise KeyError(f"No LED processor or group named '{target}'")

    def common_test_patterns(self) -> List[Tuple[str, str]]:
        """Test patterns offered by every active processor that supports patterns."""
        procs = [p for p in self.active() if CAP_TEST_PATTERN in p.capabilities()]
        if not procs:
            return []
        ids = set.intersection(*[{t["id"] for t in p.test_patterns} for p in procs])
        return [(t["id"], t.get("label", t["id"])) for t in procs[0].test_patterns if t["id"] in ids]

    def all_test_patterns(self) -> List[Tuple[str, str]]:
        seen: Dict[str, str] = {}
        for p in self.active():
            for t in p.test_patterns:
                seen.setdefault(t["id"], t.get("label", t["id"]))
        return list(seen.items())

    # ---- operations -------------------------------------------------------------------
    def _fan_out(self, label: str, fn: Callable[[LEDProcessor], None], capability: Optional[str],
                 names: Optional[List[str]] = None) -> List[str]:
        targets = [p for p in self.active() if (names is None or p.name in names)]
        if capability:
            targets = [p for p in targets if capability in p.capabilities()]
        errors: List[str] = []

        def run(p: LEDProcessor) -> None:
            try:
                fn(p)
            except TransportError as exc:
                errors.append(str(exc))
            except Exception as exc:  # never let one processor block the others
                errors.append(f"{p.name}: {exc}")

        threads = [threading.Thread(target=run, args=(p,), daemon=True) for p in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        if not targets:
            errors.append(f"No configured LED processors support {label}")
        self.bus.publish("led.command", label=label, errors=errors)
        self._publish()
        return errors

    def set_freeze(self, on: Optional[bool] = None, names: Optional[List[str]] = None) -> List[str]:
        on = (not self.frozen) if on is None else on
        return self._fan_out("freeze" if on else "unfreeze", lambda p: p.set_freeze(on), CAP_FREEZE, names)

    def set_blackout(self, on: Optional[bool] = None, names: Optional[List[str]] = None) -> List[str]:
        on = (not self.blackout) if on is None else on
        return self._fan_out("blackout" if on else "restore", lambda p: p.set_blackout(on), CAP_BLACKOUT, names)

    def set_test_pattern(self, pattern_id: Optional[str], names: Optional[List[str]] = None) -> List[str]:
        def apply(p: LEDProcessor) -> None:
            if pattern_id and not any(t["id"] == pattern_id for t in p.test_patterns):
                raise TransportError(f"{p.name}: pattern '{pattern_id}' not available")
            p.set_test_pattern(pattern_id)
        return self._fan_out(f"test pattern {pattern_id or 'off'}", apply, CAP_TEST_PATTERN, names)

    def set_locate(self, on: Optional[bool] = None, names: Optional[List[str]] = None) -> List[str]:
        on = (not self.locate) if on is None else on
        return self._fan_out("locate" if on else "locate off", lambda p: p.set_locate(on), CAP_LOCATE, names)

    def set_mode(self, target: str, mode: str, pattern: Optional[str] = None) -> List[str]:
        """Put a processor, a group or "all" into one mode. The other modes are cleared first, so
        exactly one is showing; choosing the mode they are all already in returns them to normal."""
        names = self.resolve(target)
        if not names:
            return [f"'{target}' has no enabled LED processors"]
        if mode != MODE_NORMAL and self.mode_of(names) == mode and (mode != MODE_PATTERN or not pattern
                                                                    or self.pattern_of(names) == pattern):
            mode = MODE_NORMAL
        if mode == MODE_PATTERN and not pattern:
            procs = [p for p in self.active() if p.name in names and p.test_patterns]
            if not procs:
                return [f"No test patterns on {target or 'the wall'}"]
            pattern = procs[0].test_patterns[0]["id"]
        errors: List[str] = []
        keep = {MODE_FROZEN: "frozen", MODE_BLACKOUT: "blackout", MODE_PATTERN: "test_pattern",
                MODE_LOCATE: "locate"}.get(mode)
        errors += self.restore(names, keep=keep)
        if mode == MODE_FROZEN:
            errors += self.set_freeze(True, names)
        elif mode == MODE_BLACKOUT:
            errors += self.set_blackout(True, names)
        elif mode == MODE_PATTERN:
            errors += self.set_test_pattern(pattern, names)
        elif mode == MODE_LOCATE:
            errors += self.set_locate(True, names)
        return errors

    def set_brightness(self, percent: float, names: Optional[List[str]] = None) -> List[str]:
        return self._fan_out(f"brightness {percent:.0f}%", lambda p: p.set_brightness(percent), "brightness", names)

    def restore(self, names: Optional[List[str]] = None, keep: Optional[str] = None) -> List[str]:
        """Return processors (all when ``names`` is None) to normal output: pattern off, blackout off,
        unfreeze, locate off. ``keep`` leaves one of those states alone ("frozen", "test_pattern"...)."""
        procs = [p for p in self.active() if names is None or p.name in names]
        errors: List[str] = []
        for state, off in (("test_pattern", lambda n: self.set_test_pattern(None, n)),
                           ("blackout", lambda n: self.set_blackout(False, n)),
                           ("frozen", lambda n: self.set_freeze(False, n)),
                           ("locate", lambda n: self.set_locate(False, n))):
            if state == keep:
                continue
            on = [p.name for p in procs if getattr(p.state, state)]
            if on:
                errors += off(on)
        return errors

    def restore_all(self) -> List[str]:
        """Return the whole wall to normal output: unfreeze, un-blackout, pattern off, locate off."""
        return self.restore()

    def check_all(self) -> Dict[str, bool]:
        results = {p.name: p.check_connection() for p in self.active()}
        self._publish()
        return results

    def _publish(self) -> None:
        self.bus.publish("led.state", frozen=self.frozen, blackout=self.blackout, test_pattern=self.test_pattern,
                         locate=self.locate, modes=self.modes())
