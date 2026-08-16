# Changelog

All notable changes to **moondrop_control.py** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0b5] - 2026-08-16

One bug, and it had been breaking most of what a binary release does since the first
one. Everything b3 and b4 claimed to fix about packaged installs was true of the
source tree and false of the thing people actually download.

### Fixed

- **A frozen build could not run any system program.** PyInstaller points
  `LD_LIBRARY_PATH` at the bundle's own `_internal` directory so the frozen
  interpreter finds its libraries. Every process the app spawns inherits that, and a
  system binary linked against the distribution's libraries loads the bundle's
  instead:

      pacman: /opt/hub-moon/_internal/libssl.so.3: version `OPENSSL_3.2.0' not found

  So on every `.deb`, `.rpm`, Arch package, AppImage and tarball this project has
  published: `pacman`/`dpkg`/`rpm` failed, which is why b3 and b4 still reported
  themselves as a loose tarball and still tried to overwrite `/opt` — the fix was
  there and could not run. `pkexec` failed, so the elevated install could not have
  worked either. `zenity` and `kdialog` failed, so Import and Export did nothing.
  `xdg-open` failed, so **Open log folder** did nothing. On macOS `hdiutil`, `ditto`,
  `xattr` and `codesign` all failed, so the `.dmg` updater could not have applied
  anything.

  PyInstaller saves the pre-launch value in `LD_LIBRARY_PATH_ORIG`, so the fix is to
  put it back — restore it where it was set, remove the variable where it was not —
  and hand that environment to all fifteen places this app starts a system program. A
  test walks the AST of every file that spawns one and fails on any that forgets,
  because a missing `env=` works perfectly from source and fails only in the build
  nobody runs tests against.
### Added

- **`--selftest`, and CI now runs the binary it is about to ship.** Every bug in b3,
  b4 and this release was invisible to the test suite by construction: it imports the
  source tree, where nothing is frozen, no bootloader has rewritten the library path,
  and the entry point is a function rather than a `.exe`. A bundle that gets all of
  that wrong still passes 285 tests, which is precisely what happened four times.

  `hub-moon --selftest` prints what a build can only learn about itself at runtime —
  whether it is frozen, how it was installed, and whether a child process inherits the
  bundle's library directory. `tools/smoke-frozen.py` asserts on that and
  `build-linux.yml` runs it straight after PyInstaller and before the tarball, the
  AppImage and the packages, so a broken bundle cannot become a release.

  The library-path check asks a child what it actually sees rather than running some
  tool and hoping. A probe that runs `sh` proves nothing: `sh` does not link against
  OpenSSL and survives a bundle that breaks `pacman`, `zenity` and `pkexec`. Verified
  by building deliberately without the fix and confirming the check fails.

### Fixed (continued)

- The `import hid` failure handler called `system_env()` before it was defined — it
  runs at import time, above where the helper first landed. Caught by ruff rather
  than by anybody, since it only fires on a build with no hidapi.

## [1.2.0b4] - 2026-08-16

Everything b3 turned out to be missing once it was installed rather than built. Three
of the four were invisible to the test suite in the same way — a test that checked a
constant, or a state, where the thing that was broken was the behaviour on the other
side of it.

> **Updating from 1.2.0b1, b2 or b3 on a `.deb`, `.rpm` or Arch install needs one manual
> hop.** Those builds report themselves as a loose tarball, so their updater tries to
> overwrite `/opt/hub-moon` and stops with `no permission to replace /opt/hub-moon`.
> The fix landed in b3, but b3's own updater cannot be the thing that delivers it — so
> install this one with your package manager once, and every update after it works
> from the button.

### Fixed

- **The palette picker did nothing.** `set_skin` clamped the index with
  `len(SKINS) - 1`, and `SKINS` is a *count*, not a list — so every click raised
  `TypeError: object of type 'int' has no len()` and the palette never changed. The
  test that was supposed to cover this asserted `SKINS == 4` and never called the
  callback, which is the whole lesson: both pickers are now driven through the
  callbacks the UI is actually wired to, for every value and past both ends. The
  accent picker's bound was a hardcoded `5` for the same reason and is now counted
  from the table too.
- **The window had no Wayland app id.** `class` came back empty from the compositor,
  and on Wayland that is the key to everything outside the window: a taskbar or
  switcher looks up `hub-moon.desktop` by it to find the icon, session managers group
  by it, and a compositor matches window rules against it. A Hyprland rule targeting
  `class:hub-moon` matched nothing, which looks exactly like a rule that was typed
  wrong. It is `hub-moon` now — the basename of the installed desktop entry, which is
  what makes the icon resolve — and a test keeps the two equal. X11 uses
  `StartupWMClass` instead and was never affected.
- **A manifest with no notes.** Every manifest this project has published carried
  `"notes": []`, so the What's New panel, asked to preview a version not yet
  installed, correctly had nothing to show. The cause was upstream of the app: the
  build workflows publish a GitHub release with an empty body, and
  `build-update-manifest.py` read the notes only from there. It now falls back to
  CHANGELOG.md — the same extractor that compiles `gui/notes.py`, so what you read
  before updating and what you read after come from one text. An unknown version
  still gets no notes rather than the nearest ones: showing one release's notes under
  another's heading would be the app stating something false about what you are about
  to install.
- The version matrix went red on a test that imports `gui.bridge`, which imports
  slint — and that matrix installs `hidapi` and `pytest` only, deliberately, because
  the CLI standing alone is part of what it proves. The test moved to the file that is
  gated on slint. That gate is now a plain `try/except` rather than
  `pytest.importorskip`, which from pytest 9.1 treats an importable-but-raising module
  as a hard error and needs an `exc_type=` argument the older pytests in the matrix do
  not accept.
- **No packaged install had a `hub-moon` command.** The `.deb`, `.rpm`, Arch package,
  AppImage and tarball are all the same PyInstaller bundle, whose frozen entry point
  called `gui.app.main()` and nothing else — so they shipped a window and no command
  line, while the readme and the Linux guide both documented `hub-moon --list`. The
  frozen entry now dispatches: no arguments opens the window, which is what a desktop
  launcher and a double-click do; any arguments hand over to the CLI's own `main()`,
  which already knows how to open the GUI for `--gui`. Both names go on `PATH`.

  The Windows bundle is built windowed and has no console, so what the CLI prints
  there still goes nowhere — that is not made worse, since there was no CLI to print
  at all, but it does mean this is a Linux and macOS affordance in a frozen build.
- **The `/usr/bin` launcher was a symlink into `/opt`, and that broke building from
  source on the same machine.** `python -m installer --destdir=…` resolves the target
  path against the *live* filesystem before writing it, sees the link escape
  `/usr/bin`, and refuses — so `makepkg -p PKGBUILD.local` failed with `Attempting to
  write hub-moon-gui outside of the target directory` on any machine with the binary
  package installed, which is exactly the machine a maintainer uses. Both launchers
  are ordinary wrapper scripts now.
- **A nudged fader, stepper or arrow key could not be undone.** Only a graph drag and
  a wholesale replacement ever took a snapshot, so the commonest thing anybody wants
  back was the one edit undo could not reach. Each edit is now tagged with the band
  and the knob it moved, and a snapshot is taken when that *changes* — a fader dragged
  across thirty frames stays one step, five taps of the same stepper stay one step,
  and moving to a different control starts a new one.

### Changed

- **The download tables on the website are generated from the update manifests.** They
  carry the exact URL, size and checksum the app itself verifies, so the links cannot
  drift from what is published — which is what hand-written version strings did, with
  install.html advertising 1.1.0 filenames long after they stopped being newest. Both
  channels are listed, with the beta section explaining that switching is a setting in
  the app rather than a different download.
- **The readme leads with the desktop app.** It opened as documentation for a
  command-line tool, which is what it was in 1.0.0 and has not been since — the CLI is
  now framed as the same engine for scripting and front-ends.
- **The changelog marks every release with its channel**, derived from the version
  string rather than a list anybody has to maintain.

## [1.2.0b3] - 2026-08-16

Everything in b2 that turned out to be wrong once it was installed rather than tested.
The headline is that **no Linux binary release could ever see the DAC** — a bug a year
old, hidden the whole time by the source install every piece of hardware verification
was done on.

### Fixed

- **No Linux binary release could see the DAC without root.** The tarball, the
  AppImage, the `.deb`, the `.rpm` and the Arch package are all the same PyInstaller
  bundle, built with `pip install`, which brings the manylinux `hidapi` wheel — and
  that wheel is **libusb**-backed. The shipped udev rule covered `SUBSYSTEM=="hidraw"`
  only. A libusb backend never opens `/dev/hidrawN`; it opens `/dev/bus/usb/BBB/DDD`
  and claims the interface, which needs write access to a node nothing was granting.
  So `/dev/hidraw2` was handed over perfectly and the program was never going to look
  at it.

  This hid behind the source install, where it cannot happen: a distro's own
  `python-hidapi` is hidraw-backed, so every hand-built and AUR-style install worked,
  including the one all the hardware verification was done on. `70-moondrop.rules`
  now carries both subsystems. **Anyone on a binary release needs to reinstall the
  package (or add the line by hand), reload udev, and replug the DAC** — udev does not
  revisit nodes that already exist.
- **A `.deb`, `.rpm` or Arch install reported itself as a loose tarball.** All three
  ship the PyInstaller tree into `/opt`, and `install_kind()` decided "frozen on
  Linux" meant `linux-tarball` — which is in `SELF_UPDATABLE`, so the app offered
  **Download and install** on a copy a package manager owns, and told the About panel
  the wrong thing about where it came from.

  It stopped short of damage on an ordinary machine: `/opt` is root-owned, so the
  applier refuses with "no permission to replace /opt/hub-moon" before it moves
  anything. But a button that fails is exactly what `install_kind` exists to prevent,
  and on a system where `/opt` *is* writable it would have overwritten files the
  package database still claims. It now asks `dpkg`/`rpm`/`pacman` who owns the
  executable, as the unfrozen path already did, and falls back to `linux-tarball`
  only when nobody claims it.
- **The main window was slow, and worst when it was largest.** Every `push()`
  resampled all three traces — about 8 ms of Python across eight biquads at a 980 px
  plot, on the UI thread. Two things made that much worse than it had to be: it ran on
  pushes that cannot change the curve at all (a toast, an update check finishing, a
  sheet opening, a hover), and it computed *both* views every time, when the editor
  draws three traces and the readout draws two and only one view is ever on screen.
  The traces are now keyed on what they are a function of, and only the visible view
  is sampled. A push that does not touch the curve went from 9.7 ms to 1.6 ms.
- **What's New said "from 1.2.0b2" on 1.2.0b2.** Opening it from Settings read
  "Hub Moon 1.2.0b2 · from 1.2.0b2 · already installed and running", because
  `last_run_version` is set to the running version the moment the panel is first
  shown. There is no "from" when you open it yourself, so the chip is no longer drawn.
- The new bridge tests aborted the interpreter partway through, naming a worker
  thread rather than anything in the test. A Bridge starts five workers, every row
  handed to Slint is a `PyStruct` pyo3 marks unsendable, and the cyclic collector runs
  on whichever thread trips the allocation threshold — so a worker allocating anything
  could start a collection that walks a UI-thread struct. `gui/app.py` has guarded the
  real app against this since 1.0.0; the fixture now does the same.
- **Every button was 26px too wide, with all of it after the label.** `TextBtn` sized
  itself as `inner.preferred-width + 26px`, but `inner` already contained its own
  11px padding either side — so the 26px was slack, and a `HorizontalLayout` gives its
  slack to the last stretchy child, which piled all of it up to the right of the text.
  That is the wide empty margin inside every pill in the app. The padding is now one
  number in one place, the button is the width of its contents, and the contents are
  centred in it whatever width a caller sets. `HoldBtn` had inherited the same bug and
  gets the same fix — one button style, one behaviour.

  It is also why **save to flash** was running off the right edge of the action bar:
  eight buttons were each carrying 26px they had no use for.
- **The header's three icon buttons were spaced like three unrelated things.** The
  row's 12px gap is meant to separate *groups*; every child was getting it, so the
  home, settings and reload icons sat apart in the corner. They are one group now, as
  are the three pills beside them.
- **The welcome screen's four cards.** They were fixed at 236px, so the fourth was cut
  off below about 1000px — where a tiling window manager will routinely put you. The
  first attempt at fixing that, four stretching cards with a `max-width`, was worse:
  the row ended up centred somewhere other than the rest of the screen. They now split
  a centred container of definite width, so the four shares are a division rather than
  a negotiation.
- **The palette picker overflowed into the row below it.** `SettingRow` sized itself
  from its description column alone, on the unstated assumption that a control is one
  switch tall — true for every row until a picker arrived as a column of four. A row
  is now as tall as the taller of the two things in it. The description column also
  had no explicit height, so on a tall row it stretched and pushed the label and its
  note to opposite ends.
- **The About tab closed over its own last paragraph.** The sheet sizes to
  `pane.preferred-height`, which under-reports a column of wrapping prose — a `Text`
  with `wrap: word-wrap` reports the height it would take on one line. About is eight
  paragraphs of it, so it measured about a third of what it draws.

### Changed

- **The tuning guide has figures.** It was seven paragraphs about the shape of a
  filter, asking the reader to picture something the program is perfectly able to
  draw. Each topic now opens on a diagram — three Q values at the same gain and centre
  frequency, a shelf against a peak at the same 100 Hz, the same tone reached by
  boosting once versus cutting either side, the dashed output trace under the solid
  one — and the seven topics are a rail rather than one long scroll.

  Every figure is produced by `gui/curve.py`, the sampler the editor plots with, fed
  synthetic bands. Nothing is illustrated: the page about a high shelf overflowing the
  firmware's coefficient format draws the actual overflow. Tests assert the claims the
  captions make, so a page saying "the same gain at the same frequency" cannot drift
  into being about something else.

### Added

- **A/B compare.** Hold **compare** in the action bar to hear the headphone with none
  of your tuning, release to hear it back. Pre-gain goes to 0 for the duration, and
  that is what makes it worth trusting: a curve with a +6 dB peak carries −6 dB of
  pre-gain, so leaving it in would compare your tuning against a signal six decibels
  quieter, and louder always wins a blind comparison. Both sides now peak at the same
  place, so what you are judging is tone.

  It is a hold rather than a toggle because the answer to "which am I hearing" should
  be the state of your thumb, not something you look away from the music to read. The
  DAC is written to directly; the curve on screen is never touched, nothing is marked
  dirty and nothing reaches flash.
- **Per-band mute.** The slot number on each band card turns into a mute control on
  hover — click it to hear the curve without that band, click again to bring it back
  at the filter type it had. It is written as the firmware's own `disabled` type,
  which the DAC treats as a pass-through, so this is genuinely the curve minus that
  band rather than the band flattened to a gain of zero.
- **Undo.** Distinct from **revert**, which goes all the way back to what flash holds
  — this is for the move you just wish you had not made. A drag is one step rather
  than the two hundred frames it was made of, and a step that turns out not to have
  moved anything is skipped on the way back, because a button that visibly does
  nothing is worse than no button.
- **A walkthrough that points at the real thing.** *Show me around* on the home
  screen dims the window and puts a ring around each region in turn — the control row,
  the presets, the graph, the band cards, the action bar — with a caption saying what
  is genuinely non-obvious about it rather than reading its label back. The target
  rectangle is read from the element with `absolute-position`, so it cannot drift out
  of step with the layout the way a list of hardcoded coordinates would the first time
  a row changed height.
- **The supported-device list, in the app.** It has been in the readme since 1.0.0 and
  nowhere else, which is exactly backwards: the person who needs it is holding a DAC
  that did not come up, and they are looking at this window. Eleven devices with their
  band count, custom slot and whether they take pre-gain — all read out of
  `moondrop_control`'s own tables, so a device added to the registry appears here
  without anyone updating a second list. **One is marked verified.** The other ten are
  transcribed from the vendor's registry and have never been near the hardware they
  describe, and the panel says so.
- **No DAC now lands on the home screen** instead of dropping into an editor that is
  not editing anything, with the only explanation in a toast that scrolls away. It
  names what was not found, offers the device list, and still has **Try the demo** on
  it. Only on the first miss of a session — a reload that fails while you are
  deliberately playing with the demo leaves you where you are.
- **Keyboard editing.** `1`–`8` picks a band, arrows move its gain and frequency
  (`shift` for coarse), `[` and `]` change Q, `0` flattens it, `m` mutes, `s` solos,
  and holding `space` bypasses the whole curve. `Ctrl-Z` undoes. Until now the only
  keys were `Esc`, `Ctrl-S` and `Ctrl-R`, which for a tool you use while listening —
  one hand on the headphones, eyes anywhere but the screen — was most of the work
  still on the mouse. None of them fire while a panel is open, so typing a headphone
  name into the AutoEQ search stays typing.
- **Solo.** The counterpart to mute, and the faster question most of the time: "what
  is this band doing" beats "what is everything except this band doing" when you are
  hunting for the one that owns a problem. Soloing a second band moves the solo rather
  than making you end the first, and ending a solo puts back exactly what it turned
  off — a band you had muted by hand stays muted.
- **Four palettes.** Until now the only thing you could change was the accent hue,
  which is the least consequential decision on the screen — a few hundred pixels of
  button, while the surfaces and the ink are everything else. **Moon** is the original
  warm mauve; **Paper** is warm and neutral with more contrast; **Slate** is cool
  blue-grey; **Carbon** is near-untinted and pushed furthest apart at both ends.

  Each carries a light and a dark half picked separately, for the same reason the
  accent table does — a palette that is inverted rather than re-picked comes out with
  the right lightnesses and the wrong saturations. The accent stays orthogonal: any of
  the six hues sits on any of the four grounds.

## [1.2.0b2] - 2026-08-16

The second beta. All of it is 1.2.0b1 read back off the screen — a settings panel that
did not fit its own text, and a What's New that had nothing to say on the one channel
it was built for.

### Fixed

- **What's New was empty on the beta channel.** Its notes came only from the update
  manifest, and a beta is very often a build with no published release behind it — a
  `makepkg -si` from the repo, a tag whose workflows have not finished, a wheel from a
  git URL. Every one of those announced a new version and then showed an empty panel.
  The notes for whatever is running are now compiled into the build itself, generated
  from this file by `tools/build-release-notes.py`, and the manifest is preferred only
  when it actually carries some.
- **The update heading contradicted the banner above it.** Offered a return to stable,
  the pinned banner said "Return to stable — Hub Moon 1.1.0" while the heading three
  rows below said "Hub Moon 1.1.0 is available". Two answers to one question, and the
  wrong one was the one next to the buttons.
- **Settings rows wasted a column of width and then wrapped around it.** Every control
  sat in a 200px reservation and was 190px wide, so each row had ten dead pixels down
  its right edge — enough to knock the switches out of line with the buttons below
  them, and taken out of the description, which wrapped to three lines to pay for it.
  Controls are now flush with the panel edge and the column is the width of the widest
  one.
- `pytest` could not collect anything after a `makepkg` build. The copy of the repo it
  leaves under `packaging/src/` has a second `tests/test_updater.py` in it, and two
  files with one module name is a collection error rather than a skipped duplicate.

### Added

- **What's new, before installing.** A check you clicked now opens the release's notes
  rather than only reporting that there is one, with an **Install it** button on the
  panel itself. There is also a button for it in Settings ▸ Updates, next to the one
  that skips. Nothing extra is fetched — the notes travel in the manifest the check
  just read.
- `tools/build-release-notes.py`, which turns this changelog into the short list the
  panel shows. Run it after editing this file and commit `gui/notes.py`; a test fails
  if the running version has no notes compiled in.

### Changed

- **Settings is bigger** — 760px wide against 620px, and taller. It is the only sheet
  whose rows are description-plus-control rather than a list, so width here goes
  straight into how many lines each description takes.
- The **Release notes** button on an available update was a link to the website; it is
  now **What's new**, which opens the notes in the app and works offline. The website
  is still one click away, from the panel's own **Full changelog**.

## [1.2.0b1] - 2026-08-16

The first build on the **beta** channel. Settings has a channel switch; anything on
stable stays on 1.1.0 until this has been used in anger.

The headline is that Hub Moon can now correct a pair of headphones rather than only
edit a curve. Everything else in here exists because writing tests for that turned
over three bugs that were already shipped.

### Added

- **AutoEQ, in the app.** A `headphones` button opens a searchable catalogue of
  **8,827 corrections covering 6,015 headphones** from 23 measurement sources, and
  applying one takes two clicks. The curve is shown before it goes anywhere near the
  DAC, using the same preview the community library uses.

  Every row names the rig it was measured on, and that is deliberate: six people have
  measured the HD 600 and their results do not agree, so collapsing them to one "best"
  answer would be inventing a verdict this project has no business holding. Ties are
  ordered by a stated preference, not a ranking.

  The catalogue is a 55 KB index generated by `tools/build-autoeq-index.py` and served
  from the website, rather than read from GitHub's API at runtime. The API allows 60
  requests an hour **per IP**, so two people behind one router would start getting
  403s — building this feature rate-limited the machine that wrote it. The index comes
  from a blobless shallow clone (`--filter=blob:none --no-checkout`), which has no
  rate limit at all.
- **Saved profiles.** Name the curve on screen and keep it, with no limit — the DAC's
  own slots live in its flash, are finite, and travel with the hardware instead of
  with you. Stored in the config directory, newest first, one click to load.

  A profile is byte-identical to an exported file, with a little metadata Import
  already ignores. So a profile can be mailed to somebody and imported, an export can
  be dropped into the profiles directory and appear in the list, and there is one
  schema to keep working rather than two. Loading one saved on a device with more
  bands fits it and says how many were dropped.
- **What's New**, shown once after an update. The notes come from the manifest the
  updater already downloaded, so it works with no network — which is the normal case,
  because the machine has just restarted. Not shown on a first install: there is
  nothing to have changed from, and the welcome screen is already doing that job.
- **An About panel**, with the version, install kind, OS, Python, the connected DAC
  and its firmware, and every path Hub Moon writes to — behind one **Copy system
  info** button. Eleven of the twelve supported DACs and all of USB HID on Windows
  are still untested, and closing that depends on reports from people whose hardware
  nobody here owns. This is the cheapest way to make those reports good.
- **`--version` and `--check-update`** gained a companion: `parse_peq_text()` and
  `fit_peq_to_bands()` are now real, tested functions rather than a regex loop welded
  into `main()`, so the CLI, the GUI and the tests share one implementation.
- **A test suite and CI.** 138 tests, and a matrix running them on Python 3.9–3.14
  plus Windows and macOS on every push. Until now nothing ran on a push at all — the
  three build workflows only fire on a tag, so no code was checked until a release
  was already being cut. The matrix also compiles `app.slint` (a syntax error there
  is otherwise a runtime failure with no window and no traceback), checks that every
  icon the UI names exists, and runs ruff.

### Fixed

- **AutoEQ shelves were importing as peaking filters.** `--import-rew` knew `LS` and
  `HS` but not `LSC` and `HSC` — and AutoEQ writes **every** shelf that way; a survey
  of 38 of its published profiles found exactly three tokens in use, `PK`, `LSC` and
  `HSC`. The lookup fell back to peaking for anything unrecognised, so importing any
  AutoEQ profile silently replaced both of its shelves, usually the two largest
  filters in the file, with peaks at the same frequency. An octave below the corner
  that is a difference of more than 2 dB. Unsupported shapes — notch, all-pass — are
  now reported rather than guessed at.
- **Every update command 1.1.0 printed to a package-manager install was wrong.**
  Hub Moon is published on neither PyPI nor the AUR, so `pip install --upgrade
  hub-moon` and `yay -Syu hub-moon` both fail, and there is no apt or dnf repository
  for `apt install --only-upgrade` to look in. The hints now mirror what the install
  guide actually says, and a test asserts none of them ever installs by bare package
  name again.
- **A failed update check reported "up to date".** Being unable to reach the manifest
  and being on the newest version were folded into the same `None`, so an offline
  launch asserted something it had no evidence for. Checking now raises and the
  caller decides: a check you clicked always answers, a background one stays quiet.
- `tools/build-update-manifest.py` answered a mistyped tag or a spent rate limit with
  a urllib traceback. It now says which happened, and honours `GITHUB_TOKEN` if set.

### Changed

- **Settings is a proper dialog** — a rail with **Appearance**, **Updates** and
  **About** instead of one long scroll, with a waiting update pinned above all three
  so it is never something you have to scroll to find.
- Ten filters into eight bands is now an explicit fit rather than a truncation. Every
  AutoEQ profile has ten and no device here has more than eight, so this is the
  ordinary path: the ones kept are the ones that move the curve most, and the ones
  left out are named.
- Ruff is configured to find bugs rather than restyle the codebase — its default set
  flags all ~170 of this project's `%`-format strings, which would mean touching every
  file to fix nothing.

## [1.1.0] - 2026-08-16

A release about the things around the app rather than the EQ itself: it can now tell
you when there is a newer one, it writes down what went wrong, and it no longer ends
every session with a crash dialog on Windows.

### Fixed

- **Quitting crashed the app.** Closing the window ended with *"Failed to execute
  script 'pyinstaller_entry' — 'object' object is not callable"* and a non-zero exit.

  The worker threads used `self._stop` as their queue sentinel, and `_stop` is a real
  method on `threading.Thread` that CPython calls from inside `join()`
  (`join` → `_wait_for_tstate_lock` → `self._stop()`). Assigning an `object()` over it
  meant every join raised, out of `Bridge.stop()` and through the `finally` in
  `app.main()`. The sentinel is a module-level `_QUIT` now.

  Worth spelling out, because it explains why this shipped at all: **it is invisible on
  a modern Python.** CPython 3.13 removed `_wait_for_tstate_lock`, so a 3.13+
  interpreter never calls `_stop` and the bug does not exist. The release builds are
  made with 3.12, where it always fires. It also was not Windows-specific — the Linux
  and macOS bundles raised identically, with nobody watching a windowed app's stderr.
  Windows was simply the only platform that *showed* it, via PyInstaller's own dialog.

  `Bridge.stop()` also guards each join separately now: one worker failing to be waited
  on used to skip the other two entirely.
- **The window had no icon on Windows.** The `.ico` was only ever given to
  PyInstaller, which stamps the *executable* — what Explorer and the shortcut show.
  The window itself never had one, so the titlebar and taskbar drew the shell's generic
  placeholder. `MainWindow` now sets `icon:`, from a PNG shipped in `gui/ui/`. (X11 and
  macOS get it too. Wayland has no protocol for window icons and ignores it.)
- **Import and Export did nothing on Windows and macOS.** The file chooser shelled out
  to `zenity` unconditionally — a program neither platform has. The chooser never
  appeared, and the app then reported that you had cancelled a dialog it never opened.
  Each platform now gets its own: PowerShell driving the WinForms dialog on Windows,
  `osascript`/`choose file` on macOS, zenity or kdialog on Linux (picked by which is
  actually installed, so cancelling one no longer opens the other).
- **Settings were written to the wrong place on Windows and macOS.** Every platform got
  the XDG layout, which put a Windows config in `C:\Users\you\.config\hub-moon` —
  outside the roaming profile and skipped by any backup that follows the platform's
  rules. Now `%APPDATA%\HubMoon` and `~/Library/Application Support/HubMoon`
  respectively, with an existing 1.0.0 file copied across on first run. **Linux paths
  are unchanged**; nothing moves for anyone already running it there.
- **The macOS bundle reported the wrong version.** `Info.plist` carried a hardcoded
  `0.2.0` for the whole of 1.0.0. It now comes from the same constant as everything
  else, so it cannot drift again.
- **`import hid` failing killed the app silently.** In a windowed build there is no
  stdout, so a missing hidapi meant double-clicking the icon did nothing at all, with
  no message anywhere. It now says so in a native message box before exiting.

### Added

- **Update checking, with a stable and a beta channel.** One small manifest fetched
  over HTTPS, cached for a day, compared against the running version. `stable` is
  published from the `main` branch, `beta` from `test`; the channel is a toggle in
  Settings, and a version you skip is never offered again until something newer than it
  appears.

  Every asset is checked against a SHA-256 from the manifest, and one with **no**
  checksum is refused rather than trusted. Checking defaults to on where Hub Moon ships
  the build itself and **off** where a package manager owns it; `HUB_MOON_NO_UPDATE_CHECK=1`
  turns it off everywhere without opening the app.

  What this is not: there is no code signature on any platform, so this protects
  against a corrupted download, not against someone who can serve you a manifest.
- **A log file, and somewhere for a crash to go.** Every session writes to the
  platform's log directory, `sys.excepthook` and `threading.excepthook` are installed
  so a fault on a worker thread is recorded instead of vanishing, and a fatal error
  gets a native message box naming the log — deliberately not a Slint window, since the
  failure it has to survive is the toolkit not coming up. Settings has an **Open log
  folder** button. Diagnosing the quit crash above cost a screenshot of a truncated
  dialog; that is what this is for.
- **`--version` and `--check-update`** on the CLI, the latter with `--channel`. It
  reports and installs nothing, and prints how *this* install is meant to be updated.
- **SHA256SUMS on every release.** Each workflow hashes its own assets on the runner
  that built them and attaches the list. `tools/build-update-manifest.py` reads those
  to build the manifest, and anyone can check a download by hand against the same file.

**It installs itself only where that is safe to do.** Hub Moon ships ten ways, and five
of them are owned by a package manager — an app that overwrites files `dpkg` believes it
owns has broken the system it meant to update. So the updater works out how *this* copy
got here (Inno's uninstall key, `APPIMAGE`, a `.app` bundle, `dpkg -S` / `rpm -qf` /
`pacman -Qo`, a pipx venv, a git checkout) and behaves accordingly:

| Install | What the update button does |
|---|---|
| Windows installer | downloads and runs it — Inno upgrades in place under the same AppId |
| Windows portable | swaps the extracted folder from a batch file that outlives the process |
| macOS `.app` | mounts the `.dmg`, de-quarantines and re-signs the bundle, swaps it |
| AppImage | replaces the one file with an atomic `mv` |
| Linux tarball | swaps the unpacked directory |
| deb / rpm / pacman / Nix / pip / pipx | *nothing* — it shows the command that will |

### Changed

- **One version string.** `moondrop_control.__version__` is now the only place it is
  written. `pyproject.toml` reads it with `attr:` (statically, so building a wheel does
  not need hidapi), `hub-moon.spec` reads it for the macOS bundle, the GUI shows it, and
  the updater compares against it.
- The Settings sheet is taller and scrolls, and is titled **Settings** rather than
  **Appearance**. A waiting update appears as a banner pinned above the scroll area, so
  it cannot be something you have to scroll to find.
- The sheet's footnote prints the real settings path instead of a hardcoded
  `~/.config/hub-moon/settings.json`, which had stopped being true on two platforms.

## [1.0.0] - 2026-08-15

First tagged release, so everything below is the starting feature set rather than a
diff against a previous version.

### Added

- **Packaging** — a root `pyproject.toml` makes Hub Moon a proper installable
  package (`hub-moon` CLI + `hub-moon-gui` windowed entry points, the `.slint`
  sources shipped as package data, slint an optional `[gui]` extra). On top of it,
  `packaging/` builds an installer for every platform from one PyInstaller spec:
  - **Windows** — a self-contained `.exe` plus an **Inno Setup** installer
    (`hub-moon.iss`, Start-menu/uninstaller) and a portable zip; `.ico` icon.
  - **macOS** — a `Hub Moon.app` (`BUNDLE` step, `.icns` icon) packaged as a
    drag-to-Applications **`.dmg`** for Apple Silicon. Ad-hoc signed, not notarized
    (Gatekeeper needs a right-click → Open on first launch).
  - **Linux** — a portable tarball, a single-file **AppImage** (`build-appimage.sh`),
    and **`.deb`** + **`.rpm`** via `nfpm`, plus an Arch `PKGBUILD` and a `flake.nix`
    for Nix. All install the `70-moondrop.rules` udev rule, a `.desktop` launcher
    and an icon.

  Three **GitHub Actions** workflows (`build-windows.yml`, `build-linux.yml`,
  `build-macos.yml`) build each on its own runner and attach the assets to a
  tagged Release. See `packaging/README.md`. (Verified on this Linux box: the
  wheel, Arch package, PyInstaller bundle, AppImage and `.deb`. Windows/macOS/Nix
  use the same spec/configs but need their own OS.)
- **Desktop GUI** (`--gui`) — a native window built with [Slint](https://slint.dev),
  in Hub Moon's own look: a warm rose on a light ground, or a dark palette re-picked
  rather than inverted, with six accents to choose from. One screen shows everything —
  device slot, pre-gain and global offset; eight preset pills; the response graph; all
  eight bands; and the actions. The graph carries named spectrum regions (SUB → AIR)
  behind a numbered, draggable handle per band, with three traces: a flat reference,
  the equalised curve, and a dashed `+ pre-gain (output)` curve showing what actually
  leaves the DAC. A second graph view drops the handles for the vendor chart's framing
  — one curve, normalised to a 60 dB reference. Drag a handle to move a band, scroll to
  change its Q, or use the band cards below: filter type, a gain fader, and frequency /
  Q steppers.

  **community** browses the Moondrop Hub library for the connected device; clicking a
  config opens a preview of its response curve, and nothing is written until you apply.
  There is a first-run welcome screen, a built-in tuning guide, JSON import/export via
  the desktop's own file chooser, and keyboard shortcuts (`esc` closes, `ctrl+S` saves
  to flash).

  It imports this file's engine rather than reimplementing the protocol — every write
  goes through the same `write_peq_index` and the same Q2.30 ceiling the CLI enforces,
  and a band the firmware cannot represent is clamped with its card tagged `limit`. All
  HID I/O runs on one worker thread so a read never interleaves with a write; the
  community library and the file dialogs get threads of their own. Edits are auditioned
  live on the DSP and only `save to flash` persists, while `revert` restores the last
  saved state — which a re-read cannot do, since the DSP reports whatever was written to
  it last. Icons are vector outlines generated from Material Symbols by
  `tools/build-icons.py`, so no font ships and none has to be found at runtime. With no
  DAC present it opens on a demo curve you can play with. slint is an optional extra
  (`gui/requirements.txt`), lazy-imported so the CLI keeps its hidapi-only footprint.
- **Read/write control of Moondrop USB DACs over USB HID** — parametric EQ bands,
  pre-gain, global offset, active EQ profile, and firmware version, without the
  official web app. `--list`, `--info`, `--get-peq`, `--set-peq`, `--set-pregain`,
  `--set-globalgain`, `--set-eq-index`.
- **Backup and restore** — `--export-json` / `--import-json` for a full device
  snapshot, and `--import-rew` for AutoEQ / REW `ParametricEQ.txt` files.
- **`--json`** — full device state on stdout for GUIs to consume, so a front-end
  never has to hardcode the device registry.
- **Community presets** — `--presets` browses the ~59,700-curve public library behind
  Moondrop Hub (with `--search` over the whole index, and a day-long cache under
  `~/.cache/hub_moon`), and `--preset <uuid>` pulls one down as bands. Reads need no
  account; publishing/liking are deliberately not implemented. Neither flag opens the
  DAC (strace-verified zero `/dev/hidraw` opens), so browsing can't collide with a GUI
  that is mid-write. `--registry` now also reports each device's `product_uuid`, which
  has to be hardcoded because the API's own `products/all` reports `pid: null` for all
  102 products.
- **`--no-flash` / `--save-flash`** — apply to the DSP live for auditioning, then
  persist deliberately. Writes go to flash by default.
- **`-i` interactive tuning panel** — a terminal dashboard for the same controls.
- **`--stream-status`** — hardware-level ALSA stream diagnostics (sample rate, bit
  format, supported rates). Linux-only; everything else is cross-platform.

### Protocol notes

Findings from reverse-engineering the official web app, all verified against its
JavaScript and — where marked — against real hardware. Documented in full in
[moondrop_hub_reverse_engineering.md](moondrop_hub_reverse_engineering.md).

- **Coefficient packing is Q2.30, layout `[b0, b1, b2, -a1, -a2]`**, scaled by
  2^30, computed against a fixed 96 kHz DSP rate. Confirmed byte-for-byte against
  the web app's own packing function, and corroborated by an independent
  reimplementation.
- **Filters the firmware cannot represent are refused, not wrapped.** Q2.30 spans
  only [-2, 2), which some reasonable filters exceed: a `high_shelf` above roughly
  +5 dB, a `high_shelf` with a corner below ~200 Hz at any gain, or any type at
  high gain + low Q + high frequency. The official app does not clamp, and its JS
  packs with bitwise operators that wrap modulo 2^32 — so past those limits it
  silently programs a filter unrelated to the curve it draws (a +6 dB shelf's `b1`
  wraps from -2.303 to +1.697, flipping sign). This tool refuses and reports the
  largest gain that fits. What the firmware does with a wrapped coefficient is
  untested.
- **The device registry is transcribed from the app**, correcting a scrambled
  name/ID mapping: `0x011D` is DAWN PRO2 (confirmed against real hardware),
  `0x43DA` is MOONRIVER 3, `0x011B` is Rays. "Rays Pro" does not exist. E.S. combo
  uses custom-PEQ profile slot 4; every other supported device uses 7.
- **Old Fashioned (`0x0122`) is detected but refused.** It does not use biquad
  coefficients at all — it writes PEQ through device registers as int8 gain ×10 /
  uint16 frequency / int16 Q ×1000, exposes 5 bands, and reports no pre-gain or
  global-gain support. None of this tool's commands would mean anything to it.
- **The active EQ profile is not a PEQ-mode indicator.** The official app gates
  edits on `readEQIndex() === peqIndex`, which does not hold: a DAWN PRO2 on
  firmware 1.5 reports profile 9 in *both* its EQ-off and custom-EQ modes, while
  band writes are plainly audible in custom-EQ mode. On that device the EQ toggle
  is hardware (both volume buttons) and is not reflected in any readable register —
  a sweep of every sub-command 0–254 returns identical data in both modes.
- **HID replies must be matched to their request.** A response echoes the command
  and sub-command it answers at bytes 1–2. Commands that never reply would
  otherwise leave the next read picking up the previous command's report, shifting
  every subsequent read by one and silently returning another register's data.

### Verified on hardware

DAWN PRO2 (`0x011D`, firmware 1.5): discovery, info, PEQ read, JSON export/import,
REW import, stream status idle and playing, live band writes confirmed **audible**
(an 800 Hz low-pass audibly muffled playback, restoring the band returned it to
normal), and a full flash round-trip that survived a physical unplug/replug
byte-identical — while `--no-flash` writes correctly did not survive.

Not exercised on hardware: the interactive panel (`-i`), and **every device other
than the DAWN PRO2** — those names and IDs come from the app's registry only.
