"""Wall-level LED control: one button freezes, blacks out or test-patterns every processor."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple

from operator_board.core.config import AppConfig
from operator_board.core.events import EventBus
from operator_board.led.processor import CAP_BLACKOUT, CAP_FREEZE, CAP_TEST_PATTERN, LEDProcessor
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

    def set_brightness(self, percent: float, names: Optional[List[str]] = None) -> List[str]:
        return self._fan_out(f"brightness {percent:.0f}%", lambda p: p.set_brightness(percent), "brightness", names)

    def restore_all(self) -> List[str]:
        """Return the whole wall to normal output: unfreeze, un-blackout, pattern off."""
        errors: List[str] = []
        if any(p.state.test_pattern for p in self.active()):
            errors += self.set_test_pattern(None)
        if any(p.state.blackout for p in self.active()):
            errors += self.set_blackout(False)
        if any(p.state.frozen for p in self.active()):
            errors += self.set_freeze(False)
        return errors

    def check_all(self) -> Dict[str, bool]:
        results = {p.name: p.check_connection() for p in self.active()}
        self._publish()
        return results

    def _publish(self) -> None:
        self.bus.publish("led.state", frozen=self.frozen, blackout=self.blackout, test_pattern=self.test_pattern)
