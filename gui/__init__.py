"""Hub Moon GUI — a desktop front-end for moondrop_control.py.

A native Slint app that drives the same hardware-tested engine the CLI uses: it
imports moondrop_control directly rather than reimplementing the protocol, so a
curve the graph refuses to draw is a curve the DAC would refuse to accept.

``bridge.py`` is the whole Python side — device thread, community-library thread,
file I/O thread, and the cached state. ``ui/*.slint`` is the whole view, and holds
no maths: it reports fractions and renders finished geometry.

Entry point: ``python3 moondrop_control.py --gui`` (or ``python3 -m gui``).
"""
