"""Tray menus dispatch to the UI thread; only DeviceWorker owns the DAC."""
from __future__ import annotations

import os
import sys
import threading

from .diagnostics import log


def _window_api():
    """Windows hide-to-tray without telling Slint its last window was closed.

    Slint's Python event loop exits when its last window is hidden. Keeping the
    backend window alive, while hiding its native surface, preserves timers and
    the device queue. Only this process's main Hub Moon window is eligible.
    """
    if sys.platform != "win32":
        return None, []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    windows = []

    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == 'Hub Moon':
                windows.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return user32, windows


def set_window_visible(visible):
    user32, windows = _window_api()
    if user32 is None:
        return False
    for hwnd in windows:
        user32.ShowWindow(hwnd, 9 if visible else 0)  # restore / hide
        if visible:
            user32.SetForegroundWindow(hwnd)
    return bool(windows)


class WindowCloseHandler:
    """Intercept this app's Windows close request, preserving the original proc."""
    def __init__(self, dispatch, requested):
        self.dispatch = dispatch
        self.requested = requested
        self.original = None
        self.callback = None
        self.hwnd = None
        self.api = None

    def start(self):
        import ctypes
        from ctypes import wintypes
        self.api, windows = _window_api()
        if not windows:
            return False
        self.hwnd = windows[0]
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                      wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self.set_proc = (self.api.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8
                         else self.api.SetWindowLongW)
        self.set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        self.set_proc.restype = ctypes.c_void_p
        self.api.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                           wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.api.CallWindowProcW.restype = ctypes.c_ssize_t

        @proc_type
        def procedure(hwnd, message, wparam, lparam):
            if message == 0x10:  # WM_CLOSE: ask rather than destroying the window.
                try:
                    self.dispatch(self.requested)
                    return 0
                except Exception:
                    log.warning('Could not display close options', exc_info=True)
            return self.api.CallWindowProcW(self.original, hwnd, message, wparam, lparam)

        self.callback = procedure  # Keep the native callback alive until uninstalled.
        self.original = self.set_proc(self.hwnd, -4, ctypes.cast(procedure, ctypes.c_void_p))
        if not self.original:
            raise OSError('Could not install window close handler')
        return True

    def stop(self):
        if self.original:
            import ctypes
            from ctypes import wintypes
            self.api.IsWindow.argtypes = [wintypes.HWND]
            if self.api.IsWindow(self.hwnd):
                self.set_proc(self.hwnd, -4, ctypes.c_void_p(self.original))
            self.original = None
            self.callback = None


class TrayController:
    def __init__(self, bridge, dispatch, icon_path, volume_factory=None):
        self.bridge = bridge
        self.dispatch = dispatch
        self.icon_path = icon_path
        self.icon = None
        self._api = None
        self._signature = None
        self.active = False
        self.volume_factory = volume_factory
        self.volume_window = None
        self.close_handler = None

    def start(self):
        try:
            import pystray
            from PIL import Image
            if not pystray.Icon.HAS_MENU:
                raise RuntimeError('this desktop tray backend does not support menus')
            self._api = pystray
            with Image.open(self.icon_path) as image:
                image = image.convert('RGBA')
            self.icon = pystray.Icon('hub-moon', image, 'Hub Moon')
            self.update()
            ready = threading.Event()
            errors = []

            def setup(icon):
                try:
                    icon.visible = True
                except Exception as exc:
                    errors.append(exc)
                finally:
                    ready.set()

            self.icon.run_detached(setup)
            if not ready.wait(2) or errors:
                raise RuntimeError('tray icon could not become visible')
            self.active = True
            return True
        except Exception:
            log.warning('System tray unavailable; keeping normal window behavior', exc_info=True)
            self.stop()
            return False

    def _action(self, fn, *args):
        def selected(*_):
            # Never read a Slint property or call Bridge from the native tray thread.
            self.dispatch(lambda: fn(*args))
        return selected

    def update(self):
        if self.icon is None:
            return
        b = self.bridge
        self._sync_volume_window()
        profiles = tuple(str(p['name']) for p in b.prof_rows)
        presets = tuple(b.tray_preset_names())
        state = (b.connected, b.busy, b.physical_eq_mode, b.supports_physical_volume,
                 b.physical_volume, b.dirty, profiles, presets, b.active_preset,
                 b.device_name)
        if state == self._signature:
            return
        self._signature = state
        item, menu = self._api.MenuItem, self._api.Menu
        available = b.connected and not b.busy
        mode = b.physical_eq_mode
        self.icon.title = 'Hub Moon · ' + (b.device_name if b.connected else 'No DAC')
        preset_items = [item(name, self._action(b.tray_apply_preset, i),
                             checked=(lambda _, selected=name == b.active_preset: selected),
                             radio=True, enabled=available)
                        for i, name in enumerate(presets)]
        profile_items = [item(name, self._action(b.tray_apply_profile, name), enabled=available)
                         for name in profiles]
        if not profile_items:
            profile_items = [item('No saved profiles', None, enabled=False)]
        entries = [item('Show Hub Moon', self._action(self.show), default=True)]
        if sys.platform == 'win32':
            entries.append(item('Minimize to tray', self._action(self.hide)))
        entries.extend([
            menu.SEPARATOR,
            item('Custom EQ', self._action(b.toggle_custom_eq),
                 checked=lambda _: mode >= 0 and mode != 8,
                 enabled=available and mode >= 0),
            item('EQ presets', menu(*preset_items), enabled=available),
            item('Saved profiles', menu(*profile_items), enabled=available),
        ])
        if b.supports_physical_volume:
            entries.append(item('Volume… (%.1f dB)' % b.physical_volume,
                                self._action(self.show_volume), enabled=available))
        entries.extend([
            item('Save to DAC', self._action(b.save_to_flash),
                 enabled=available and b.dirty and mode != 8),
            menu.SEPARATOR,
            item('Quit Hub Moon', self._action(self.quit)),
        ])
        self.icon.menu = menu(*entries)

    def show(self):
        if not set_window_visible(True):
            self.bridge.win.show()

    def hide(self):
        if self.active and not set_window_visible(False):
            self.bridge.toast('Could not minimize this window to the tray.', True)
            self.bridge.push()

    def quit(self):
        import slint
        slint.quit_event_loop()

    def _sync_volume_window(self):
        if self.volume_window is not None:
            b = self.bridge
            self.volume_window.volume = b.physical_volume
            self.volume_window.available = b.connected and b.supports_physical_volume
            self.volume_window.device_name = b.device_name
            self.volume_window.Theme.skin_index = b.win.Theme.skin_index
            self.volume_window.Theme.dark = b.win.Theme.dark
            self.volume_window.Theme.motion = b.win.Theme.motion

    def show_volume(self):
        if not self.bridge.connected or not self.bridge.supports_physical_volume:
            return
        if self.volume_window is None and self.volume_factory is not None:
            self.volume_window = self.volume_factory()
            self.volume_window.set_physical_volume = self.bridge.set_physical_volume
        if self.volume_window is not None:
            self._sync_volume_window()
            self.volume_window.show()

    def stop(self):
        self.active = False
        if self.close_handler is not None:
            self.close_handler.stop()
        if self.volume_window is not None:
            self.volume_window.hide()
        if self.icon is not None:
            self.icon.stop()
            self.icon = None
