#!/bin/sh
# Installed as both /usr/bin/hub-moon and /usr/bin/hub-moon-gui by the .deb, .rpm and
# Arch packages, which put the frozen bundle in /opt.
#
# The bundle dispatches on its own arguments — none opens the window, any hands over
# to the CLI — so one launcher serves both names and `hub-moon --list` works from a
# packaged install for the first time.
exec /opt/hub-moon/hub-moon "$@"
