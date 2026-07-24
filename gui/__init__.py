"""Hub Moon GUI — a desktop front-end for moondrop_control.py.

A PySide6 / QML app that drives the same hardware-tested engine the CLI uses:
it imports moondrop_control directly rather than reimplementing the protocol.
The interface is modelled on MOONDROP's own Sound-Tuning Tool (the Hub web app):
a near-black theme with a blue accent, the red "equalized" curve over a purple
"flat" reference, and a horizontal Filter / Gain / Frequency / Q band grid.

Entry point: ``python3 moondrop_control.py --gui`` (or ``python3 -m gui``).
"""
