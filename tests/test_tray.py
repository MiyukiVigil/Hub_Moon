"""Native tray callbacks must marshal to the UI and never open another DAC."""
from types import SimpleNamespace
from gui.tray import TrayController


class Item:
    def __init__(self, text, action, **kwargs):
        self.text, self.action = text, action
        self.__dict__.update(kwargs)


class Menu:
    SEPARATOR = object()
    def __init__(self, *items): self.items = items


def tray():
    actions, queued = [], []
    bridge = SimpleNamespace(connected=True, busy=False, physical_eq_mode=8,
        supports_physical_volume=True, physical_volume=-20, dirty=True,
        prof_rows=[{'name': 'My headphones'}], active_preset='', device_name='DAWN PRO2',
        tray_preset_names=lambda: ['Flat', 'Bass'],
        toggle_custom_eq=lambda: actions.append('toggle'),
        tray_apply_preset=lambda n: actions.append(('preset', n)),
        tray_apply_profile=lambda n: actions.append(('profile', n)),
        tray_volume_step=lambda n: actions.append(('volume', n)),
        save_to_flash=lambda: actions.append('save'))
    controller = TrayController(bridge, queued.append, '')
    controller._api = SimpleNamespace(MenuItem=Item, Menu=Menu)
    controller.icon = SimpleNamespace(title='', menu=None)
    controller.update()
    return controller, bridge, actions, queued


def find(menu, text):
    return next(i for i in menu.items if isinstance(i, Item) and i.text == text)


def test_native_callback_only_queues_ui_action():
    c, _, actions, queued = tray()
    find(c.icon.menu, 'Custom EQ').action(None, None)
    assert actions == []
    queued.pop()()
    assert actions == ['toggle']


def test_presets_and_profiles_preserve_the_selected_arguments():
    c, _, actions, queued = tray()
    find(find(c.icon.menu, 'EQ presets').action, 'Bass').action()
    find(find(c.icon.menu, 'Saved profiles').action, 'My headphones').action()
    assert actions == []
    for callback in queued:
        callback()
    assert actions == [('preset', 1), ('profile', 'My headphones')]


def test_volume_menu_opens_synced_slider_on_ui_thread():
    c, b, actions, queued = tray()
    b.win = SimpleNamespace(Theme=SimpleNamespace(skin_index=2, dark=True, motion=0.5))
    b.set_physical_volume = lambda db: actions.append(('volume', db))
    popup = SimpleNamespace(Theme=SimpleNamespace(), show=lambda: actions.append('shown'))
    c.volume_factory = lambda: popup
    find(c.icon.menu, 'Volume… (-20.0 dB)').action()
    assert c.volume_window is None
    queued.pop()()
    assert c.volume_window is popup
    assert popup.volume == -20
    assert popup.available
    assert popup.Theme.skin_index == 2 and popup.Theme.dark
    popup.set_physical_volume(-32)
    assert actions[-1] == ('volume', -32)
    b.physical_volume = -35
    c.update()
    assert popup.volume == -35
    b.connected = False
    c.update()
    assert not popup.available


def test_physical_mode_change_updates_checkmark_and_flash_availability():
    c, b, _, _ = tray()
    assert not find(c.icon.menu, 'Custom EQ').checked(None)
    assert not find(c.icon.menu, 'Save to DAC').enabled
    b.physical_eq_mode = 9
    c.update()
    assert find(c.icon.menu, 'Custom EQ').checked(None)
    assert find(c.icon.menu, 'Save to DAC').enabled


def test_disconnect_and_busy_disable_all_device_actions():
    c, b, _, _ = tray()
    for field in ('connected', 'busy'):
        b.connected, b.busy = True, False
        setattr(b, field, field == 'busy')
        c.update()
        for name in ('Custom EQ', 'EQ presets', 'Saved profiles', 'Save to DAC'):
            assert not find(c.icon.menu, name).enabled


def test_profile_changes_refresh_menu_and_empty_library_is_explicit():
    c, b, _, _ = tray()
    b.prof_rows = []
    c.update()
    entry = find(find(c.icon.menu, 'Saved profiles').action, 'No saved profiles')
    assert not entry.enabled


def test_unchanged_state_does_not_rebuild_native_menu():
    c, _, _, _ = tray()
    original = c.icon.menu
    c.update()
    assert c.icon.menu is original
