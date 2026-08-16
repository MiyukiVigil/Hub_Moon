"""Release notes, generated from CHANGELOG.md — do not edit by hand.

    python3 tools/build-release-notes.py

Read by gui/updater.release_notes() when no downloaded manifest describes the version
that is running, which is the normal case for anything built from the repo rather than
installed from a release.
"""


NOTES = {
    '1.2.0b3': [
        'No Linux binary release could see the DAC without root.',
        'A .deb, .rpm or Arch install reported itself as a loose tarball.',
        'The main window was slow, and worst when it was largest.',
        'What\'s New said "from 1.2.0b2" on 1.2.0b2.',
        'The version matrix went red on a test that imports gui.bridge, which imports slint — and that matrix installs hidapi and pytest only, deliberately, because the CLI standing alone is part of…',
        'The new bridge tests aborted the interpreter partway through, naming a worker thread rather than anything in the test.',
        'Every button was 26px too wide, with all of it after the label.',
        "The header's three icon buttons were spaced like three unrelated things.",
        "The welcome screen's four cards. They were fixed at 236px, so the fourth was cut off below about 1000px — where a tiling window manager will routinely put you.",
        'The palette picker overflowed into the row below it.',
        'The About tab closed over its own last paragraph.',
        'A/B compare. Hold compare in the action bar to hear the headphone with none of your tuning, release to hear it back.',
    ],
    '1.2.0b2': [
        "What's New was empty on the beta channel.",
        'The update heading contradicted the banner above it.',
        'Settings rows wasted a column of width and then wrapped around it.',
        'pytest could not collect anything after a makepkg build.',
        "What's new, before installing. A check you clicked now opens the release's notes rather than only reporting that there is one, with an Install it button on the panel itself.",
        'tools/build-release-notes.py, which turns this changelog into the short list the panel shows.',
        'Settings is bigger — 760px wide against 620px, and taller.',
        "The Release notes button on an available update was a link to the website; it is now What's new, which opens the notes in the app and works offline.",
    ],
    '1.2.0b1': [
        'AutoEQ shelves were importing as peaking filters.',
        'Every update command 1.1.0 printed to a package-manager install was wrong.',
        'A failed update check reported "up to date".',
        'tools/build-update-manifest.py answered a mistyped tag or a spent rate limit with a urllib traceback.',
        'AutoEQ, in the app. A headphones button opens a searchable catalogue of 8,827 corrections covering 6,015 headphones from 23 measurement sources, and applying one takes two clicks.',
        "Saved profiles. Name the curve on screen and keep it, with no limit — the DAC's own slots live in its flash, are finite, and travel with the hardware instead of with you.",
        "What's New, shown once after an update.",
        'An About panel, with the version, install kind, OS, Python, the connected DAC and its firmware, and every path Hub Moon writes to — behind one Copy system info button.',
        '--version and --check-update gained a companion: parse_peq_text() and fit_peq_to_bands() are now real, tested functions rather than a regex loop welded into main(), so the CLI, the GUI and…',
        'A test suite and CI. 138 tests, and a matrix running them on Python 3.9–3.14 plus Windows and macOS on every push.',
        'Settings is a proper dialog — a rail with Appearance, Updates and About instead of one long scroll, with a waiting update pinned above all three so it is never something you have to scroll…',
        'Ten filters into eight bands is now an explicit fit rather than a truncation.',
    ],
    '1.1.0': [
        'Quitting crashed the app. Closing the window ended with "Failed to execute script \'pyinstaller_entry\' — \'object\' object is not callable" and a non-zero exit.',
        'The window had no icon on Windows.',
        'Import and Export did nothing on Windows and macOS.',
        'Settings were written to the wrong place on Windows and macOS.',
        'The macOS bundle reported the wrong version.',
        'import hid failing killed the app silently.',
        'Update checking, with a stable and a beta channel.',
        'A log file, and somewhere for a crash to go.',
        '--version and --check-update on the CLI, the latter with --channel.',
        'SHA256SUMS on every release. Each workflow hashes its own assets on the runner that built them and attaches the list.',
        'One version string. moondrop_control.__version__ is now the only place it is written.',
        'The Settings sheet is taller and scrolls, and is titled Settings rather than Appearance.',
    ],
    '1.0.0': [
        'Packaging — a root pyproject.toml makes Hub Moon a proper installable package (hub-moon CLI + hub-moon-gui windowed entry points, the .slint sources shipped as package data, slint an option…',
        "Desktop GUI (--gui) — a native window built with Slint, in Hub Moon's own look: a warm rose on a light ground, or a dark palette re-picked rather than inverted, with six accents to choose f…",
        'Read/write control of Moondrop USB DACs over USB HID',
        'Backup and restore — --export-json / --import-json for a full device snapshot, and --import-rew for AutoEQ / REW ParametricEQ.txt files.',
        '--json — full device state on stdout for GUIs to consume, so a front-end never has to hardcode the device registry.',
        'Community presets — --presets browses the ~59,700-curve public library behind Moondrop Hub (with --search over the whole index, and a day-long cache under ~/.cache/hub_moon), and --preset <…',
        '--no-flash / --save-flash — apply to the DSP live for auditioning, then persist deliberately.',
        '-i interactive tuning panel — a terminal dashboard for the same controls.',
        '--stream-status — hardware-level ALSA stream diagnostics (sample rate, bit format, supported rates).',
    ],
}
