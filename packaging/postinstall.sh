#!/bin/sh
# Runs after a .deb/.rpm install: apply the udev rule so the DAC is reachable
# right away, and refresh the desktop/icon caches. All best-effort.
set -e
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q 2>/dev/null || true
exit 0
