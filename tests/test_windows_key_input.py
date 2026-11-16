from types import SimpleNamespace
from threading import Lock
import unittest
from unittest.mock import patch

from devorchestra import adaptive_play
from devorchestra import windows_key_input as keyboard
from devorchestra.lease import Blocked


class GuardedKeyboardTests(unittest.TestCase):
    def test_desktop_scope_is_explicit_and_preserves_the_legacy_key_limit(self):
        from json import dumps

        legacy = adaptive_play.approval(foreground=True)
        self.assertNotIn("desktop_hold_ms", legacy)
        allowed = adaptive_play.approval(foreground=True, desktop_hold_ms=1000)
        adaptive_play.validate_policy(allowed)
        self.assertEqual(30, allowed["capture_fps"])
        for change in ({"foreground": False}, {"desktop_hold_ms": 1001}, {"capture_fps": None}):
            with self.assertRaises(Blocked):
                adaptive_play.validate_policy({**allowed, **change})
        raw = dumps(dict(action="keys", keys=["D", "SPACE"], ms=1000, x=0, y=0, observed="Playfield"))
        with self.assertRaises(Blocked):
            adaptive_play.parse(raw, (0, 0, 640, 360), legacy["actions"])
        adaptive_play.parse(raw, (0, 0, 640, 360), allowed["actions"], desktop_hold_ms=1000)

    def test_a_hold_settles_and_interruption_releases_both_keys(self):
        for interruption in (None, "focus lost", "cancelled", "activity changed", "worker exception"):
            with self.subTest(interruption=interruption):
                driver = object.__new__(keyboard.Keyboard)
                driver._pressed, driver._usable, driver._stamp = [], True, 1
                driver.max_hold_ms, driver._release_lock = 1000, Lock()
                driver.cleanup, driver._desktop, driver._input_desktop = "NO_INPUT", "test", lambda: "test"
                driver._window = SimpleNamespace(thread=7)
                driver._layout = lambda _: 7
                driver._map = lambda scan, *_: scan
                driver._state = lambda scan: 0x8000 if scan in down else 0
                driver._ticks = lambda: round(now[0] * 1000)
                driver._last_input = lambda: last[0]
                now, last, down, events = [1.0], [1], set(), []

                def target(*, holding=False):
                    if down:
                        self.assertTrue(holding, "A held key must never wait for the full execution guard")
                    elif events and not holding:
                        now[0] += 0.25  # Post-release audit may be slower than the watchdog grace period.
                    if now[0] >= 1.15 and interruption in {"focus lost", "cancelled"}:
                        raise Blocked(interruption)
                    if now[0] >= 1.15 and interruption == "worker exception":
                        raise RuntimeError(interruption)

                def emit(scans, *, up, stamp=0):
                    self.assertEqual(0, stamp)
                    (down.difference_update if up else down.update)(scans)
                    events.append((list(scans), up))
                    last[0] = round(now[0] * 1000) + 7
                    return len(scans)

                def sleep(seconds):
                    now[0] += seconds
                    if now[0] >= 1.15 and interruption == "activity changed":
                        last[0] = 999

                driver._target, driver._emit = target, emit
                with patch.object(keyboard, "monotonic", lambda: now[0]), patch.object(keyboard, "sleep", sleep):
                    if interruption:
                        with self.assertRaises((Blocked, RuntimeError)):
                            driver.hold(["D", "SPACE"], 1000)
                        self.assertFalse(driver._usable)
                    else:
                        self.assertEqual(1000, driver.hold(["D", "SPACE"], 1000))
                        self.assertTrue(driver._usable)
                self.assertEqual([([0x20, 0x39], False), ([0x39], True), ([0x20], True)], events)
                self.assertFalse(down)
                self.assertFalse(driver._pressed)

    def test_unconfirmed_release_is_retained_for_recovery(self):
        driver = object.__new__(keyboard.Keyboard)
        driver._pressed = [0x20, 0x39]
        driver._release_lock = Lock()
        driver._emit = lambda scans, **_: int(scans == [0x20])
        with self.assertRaises(keyboard.ReleaseUnconfirmed):
            driver.release()
        self.assertEqual([0x39], driver._pressed)

    def test_submission_rejects_activity_outside_own_batch_and_handles_tick_wrap(self):
        driver = object.__new__(keyboard.Keyboard)
        driver._state = lambda _: 0x8000
        driver._last_input = lambda: 2
        driver._confirm_keyboard(0xFFFFFFFE, 0xFFFFFFFF, 1, [68], down=True)
        self.assertEqual(2, driver._stamp)
        driver._last_input = lambda: 500
        with self.assertRaisesRegex(Blocked, "another input event"):
            driver._confirm_keyboard(2, 100, 100, [68], down=True)
