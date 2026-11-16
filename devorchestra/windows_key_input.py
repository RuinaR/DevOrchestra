"""Opt-in keyboard input for an approved foreground window."""
import ctypes as c
from ctypes import wintypes as w
from threading import Event, Lock, Thread
from time import monotonic, sleep

from .gui_verification import signature
from .lease import Blocked

SCAN_CODES = {"W": 0x11, "A": 0x1E, "S": 0x1F, "D": 0x20, "SPACE": 0x39,
              "LEFT": 0xE04B, "UP": 0xE048, "RIGHT": 0xE04D, "DOWN": 0xE050}
MAX_HOLD_MS = 1000
POLL_SECONDS = 0.01
INPUT_ACK_SECONDS = 0.05
TICK_RESOLUTION_MS = 16


class ReleaseUnconfirmed(Blocked):
    pass


class _Keyboard(c.Structure):
    _fields_ = [("vk", w.WORD), ("scan", w.WORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", c.c_size_t)]


class _Mouse(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG), ("data", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", c.c_size_t)]


class _Payload(c.Union):
    _fields_ = [("keyboard", _Keyboard), ("mouse", _Mouse)]


class _Input(c.Structure):
    _fields_ = [("kind", w.DWORD), ("payload", _Payload)]


class _LastInput(c.Structure):
    _fields_ = [("size", w.UINT), ("tick", w.DWORD)]


class _ThreadInfo(c.Structure):
    _fields_ = [("size", w.DWORD), ("flags", w.DWORD)] + [
        (name, w.HWND) for name in ("active", "focus", "capture", "menu", "move", "caret")
    ] + [("caret_rect", w.RECT)]


class Keyboard:
    def __init__(self, window, verify_execution, *, max_hold_ms=MAX_HOLD_MS):
        user = c.WinDLL("user32", winmode=0x800, use_last_error=True)
        kernel = c.WinDLL("kernel32", winmode=0x800, use_last_error=True)
        self._window, self._verify_execution = window, verify_execution
        self._send = signature(user, "SendInput", w.UINT, w.UINT, c.POINTER(_Input), c.c_int)
        self._last = signature(user, "GetLastInputInfo", w.BOOL, c.POINTER(_LastInput))
        self._focus = signature(user, "GetGUIThreadInfo", w.BOOL, w.DWORD, c.POINTER(_ThreadInfo))
        self._state = signature(user, "GetAsyncKeyState", w.SHORT, c.c_int)
        self._layout = signature(user, "GetKeyboardLayout", w.HANDLE, w.DWORD)
        self._map = signature(user, "MapVirtualKeyExW", w.UINT, w.UINT, w.UINT, w.HANDLE)
        self._ticks = signature(kernel, "GetTickCount", w.DWORD)
        self._open_desktop = signature(user, "OpenInputDesktop", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        self._close_desktop = signature(user, "CloseDesktop", w.BOOL, w.HANDLE)
        self._get_desktop = signature(user, "GetThreadDesktop", w.HANDLE, w.DWORD)
        self._current_thread = signature(kernel, "GetCurrentThreadId", w.DWORD)
        self._get_object = signature(user, "GetUserObjectInformationW", w.BOOL, w.HANDLE, c.c_int,
                                     c.c_void_p, w.DWORD, c.POINTER(w.DWORD))
        self._hit = signature(user, "WindowFromPoint", w.HWND, w.POINT)
        self._metrics = signature(user, "GetSystemMetrics", c.c_int, c.c_int)
        self.max_hold_ms = max_hold_ms
        self._release_lock = Lock()
        self._desktop = self._active_controller_desktop()
        self._pressed = []
        self._usable = True
        self.cleanup = "NO_INPUT"
        self._target()
        self._stamp = self._last_input()

    def _desktop_name(self, handle):
        name = c.create_unicode_buffer(256)
        if not handle or not self._get_object(handle, 2, name, c.sizeof(name), None):
            raise Blocked("Windows input desktop could not be verified")
        return name.value

    def _input_desktop(self):
        desktop = self._open_desktop(0, False, 0x0001)
        if not desktop:
            raise Blocked("Windows input desktop is unavailable")
        try:
            return self._desktop_name(desktop)
        finally:
            if not self._close_desktop(desktop):
                raise Blocked("Windows input desktop handle could not be released")

    def _active_controller_desktop(self):
        # MXC denies querying another process's thread desktop. Verify the
        # actual input desktop here and bind the focused window separately.
        desktop = self._get_desktop(self._current_thread())
        active = w.BOOL()
        if (not desktop or not self._get_object(desktop, 6, c.byref(active), c.sizeof(active), None)
                or not active.value):
            raise Blocked("Controller is not on the active Windows input desktop")
        return self._desktop_name(desktop)

    def _target(self):
        window = self._window
        window.check()
        self._verify_execution()
        root = window.owned("root")
        if self._input_desktop() != self._desktop or self._active_controller_desktop() != self._desktop:
            raise Blocked("Owned game is not on the Windows input desktop")
        info = _ThreadInfo(size=c.sizeof(_ThreadInfo))
        if (window.foreground() != root or not self._focus(0, c.byref(info))
                or info.active != root or info.focus != root or info.flags & 0x1F
                or window.foreground() != root):
            raise Blocked("Scan-code input requires the focused owned game window")

    def _last_input(self):
        info = _LastInput(size=c.sizeof(_LastInput))
        if not self._last(c.byref(info)):
            raise Blocked("Windows input activity could not be checked")
        return info.tick

    def _check(self, phase="before next action or observation"):
        self._target()
        observed = self._last_input()
        if observed != self._stamp:
            raise Blocked(f"Scan-code input stopped after another input event "
                          f"({phase}; expected {self._stamp}, observed {observed})")

    def _emit(self, scans, *, up, stamp=0):
        flags = 0x0008 | (0x0002 if up else 0)
        events = (_Input * len(scans))(*(
            _Input(1, _Payload(keyboard=_Keyboard(scan=scan & 0xFF, time=stamp,
                                                    flags=flags | (0x0001 if scan > 0xFF else 0))))
            for scan in scans))
        return self._send(len(events), events, c.sizeof(_Input))

    def _confirm_release(self, stamp, previous, virtual_keys):
        until = monotonic() + 0.05
        while True:
            self._target()
            last = self._last_input()
            if last == stamp and not any(self._state(code) & 0x8000 for code in virtual_keys):
                break
            if last not in (previous, stamp) or monotonic() >= until:
                raise Blocked("Windows did not confirm input release before another action")
            sleep(POLL_SECONDS)
        self._stamp = stamp
        self._usable = True

    def _verify_up(self, virtual_keys):
        until = monotonic() + 0.05
        try:
            while self._input_desktop() == self._desktop:
                if not any(self._state(code) & 0x8000 for code in virtual_keys):
                    self.cleanup = "CONFIRMED"
                    return
                if monotonic() >= until:
                    break
                sleep(POLL_SECONDS)
        except (Blocked, OSError):
            pass
        raise ReleaseUnconfirmed("Windows input release state could not be verified")

    def _confirm_keyboard(self, previous, first_tick, last_tick, virtual_keys, *, down):
        # Only during our submission boundary can the session's input tick
        # advance. Compare modulo DWORD, including one Windows timer quantum.
        span = ((last_tick - first_tick) & 0xFFFFFFFF) + TICK_RESOLUTION_MS
        if span > round(INPUT_ACK_SECONDS * 1000) + TICK_RESOLUTION_MS:
            raise Blocked("Windows scan-code submission exceeded its confirmation window")
        deadline = monotonic() + INPUT_ACK_SECONDS
        edge = "press" if down else "release"
        while True:
            observed = self._last_input()
            in_submission = ((observed - first_tick) & 0xFFFFFFFF) <= span
            if observed != previous and not in_submission:
                raise Blocked(f"Scan-code input stopped after another input event "
                              f"(confirming {edge}; previous {previous}, submitted {first_tick}–{last_tick}, "
                              f"observed {observed})")
            if in_submission and all(bool(self._state(code) & 0x8000) == down for code in virtual_keys):
                self._stamp = observed
                return
            if monotonic() >= deadline:
                raise Blocked(f"Windows did not confirm scan-code {edge} and key state "
                              f"(submitted {first_tick}–{last_tick}; observed {observed})")
            self._target()
            sleep(POLL_SECONDS)

    def hold(self, keys, milliseconds):
        if (not 1 <= len(keys) <= 2 or len(set(keys)) != len(keys) or any(key not in SCAN_CODES for key in keys)
                or type(milliseconds) is not int or not 30 <= milliseconds <= self.max_hold_ms
                or self._pressed or not self._usable):
            raise Blocked("Invalid guarded scan-code input")
        scans = [SCAN_CODES[key] for key in keys]
        self._check("before preparing the key hold")
        layout = self._layout(self._window.thread)
        virtual_keys = [self._map(scan, 3, layout) for scan in scans]
        if not all(virtual_keys) or any(self._state(code) & 0x8000 for code in (
                *virtual_keys, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise Blocked("A physical key or system modifier is already held")

        self._check("before arming the key hold")
        self._usable = False
        finished, expired, watchdog_failed = Event(), Event(), Event()

        def watch():
            if not finished.wait(max(0, until + 0.075 - monotonic())):
                expired.set()
                try:
                    self.release()
                except Exception:
                    watchdog_failed.set()

        watchdog = Thread(target=watch, name="owned-key-release-watchdog", daemon=True)
        try:
            with self._release_lock:
                self._check("before submitting the key press")
                previous = self._stamp
                started = monotonic()
                until = started + milliseconds / 1000
                watchdog.start()
                first_tick = self._ticks()
                c.set_last_error(0)
                count = self._emit(scans, up=False)
                winerror = c.get_last_error()
                last_tick = self._ticks()
                self._pressed.extend(scans[:count])
                if count:
                    self.cleanup = "RELEASE_UNCONFIRMED"
            if count != len(scans):
                raise Blocked(f"SendInput press accepted {count}/{len(scans)} events (Win32 error {winerror})")
            self._confirm_keyboard(previous, first_tick, last_tick, virtual_keys, down=True)
            while True:
                if expired.is_set():
                    raise Blocked("Input watchdog stopped an overdue hold")
                if monotonic() >= until:
                    break
                try:
                    self._check("during the key hold")
                except Blocked as error:
                    if expired.is_set():
                        raise Blocked("Input watchdog released the held key during a slow guard check") from error
                    raise
                if expired.is_set():
                    raise Blocked("Input watchdog released the held key during a slow guard check")
                if not all(self._state(code) & 0x8000 for code in virtual_keys):
                    raise Blocked("An approved scan-code key was released during the hold")
                remaining = until - monotonic()
                if remaining <= 0:
                    break
                sleep(min(POLL_SECONDS, remaining))
        finally:
            # Emergency key-up is necessarily session-wide, even after focus or cancellation changes.
            # Attempt each accepted key independently so a failure does not skip the other release.
            release_before = self._ticks()
            try:
                self.release()
                release_after = self._ticks()
            finally:
                finished.set()
                if watchdog.ident is not None:
                    watchdog.join(0.2)
                if watchdog.is_alive() or watchdog_failed.is_set() or self._pressed:
                    raise ReleaseUnconfirmed("Input watchdog or key release is unresolved")
            if self.cleanup == "RELEASE_UNCONFIRMED":
                self._verify_up(virtual_keys)
        if expired.is_set():
            raise Blocked("Input watchdog stopped an overdue hold")
        self._target()
        self._confirm_keyboard(self._stamp, release_before, release_after, virtual_keys, down=False)
        self._usable = True
        return round((monotonic() - started) * 1000)

    def click(self, x, y):
        if not self._usable:
            raise Blocked("Desktop input is no longer available")
        self._check()
        window = self._window
        root, child, _ = window._client_target(x, y)
        point = w.POINT(x, y)
        if not window.client_to_screen(root, c.byref(point)) or self._hit(point) != child:
            raise Blocked("Desktop click is obscured or outside the owned control")
        if any(self._state(code) & 0x8000 for code in (0x01, 0x02, 0x04, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise Blocked("A mouse button or system modifier is already held")
        left, top, width, height = (self._metrics(index) for index in (76, 77, 78, 79))
        if not (width > 1 and height > 1 and left <= point.x < left + width and top <= point.y < top + height):
            raise Blocked("Desktop click is outside the virtual display")
        previous, stamp = self._stamp, self._ticks()
        if not stamp or stamp == previous:
            raise Blocked("Desktop click activity timestamp is ambiguous")
        events = (_Input * 3)(
            _Input(0, _Payload(mouse=_Mouse(((point.x - left) * 65536 + 32768) // width,
                                            ((point.y - top) * 65536 + 32768) // height, 0, 0xC001, stamp))),
            _Input(0, _Payload(mouse=_Mouse(flags=0x0002, time=stamp))),
            _Input(0, _Payload(mouse=_Mouse(flags=0x0004, time=stamp))))
        self._check()
        if self._hit(point) != child:
            raise Blocked("Desktop click target changed")
        self._usable = False
        count = self._send(3, events, c.sizeof(_Input))
        if count >= 2:
            self.cleanup = "RELEASE_UNCONFIRMED"
            if count == 2 and self._send(1, c.byref(events[2]), c.sizeof(_Input)) != 1:
                raise ReleaseUnconfirmed("Desktop click release is unresolved")
            self._verify_up((0x01,))
        if count != 3:
            raise Blocked("Windows did not accept the complete desktop click")
        self._confirm_release(stamp, previous, (0x01,))

    def release(self, *, stamp=0):
        with self._release_lock:
            for scan in reversed(self._pressed.copy()):
                if self._emit([scan], up=True, stamp=stamp) == 1:
                    self._pressed.remove(scan)
            if self._pressed:
                raise ReleaseUnconfirmed("Windows scan-code key release is unresolved")
