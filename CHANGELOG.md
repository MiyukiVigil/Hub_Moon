# Changelog

All notable changes to **moondrop_control.py** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0b1] - 2026-08-18

The first build on the **2.0** line, and on the **beta** channel — stable stays on
1.2.0.

2.0 is about discovery and distribution: finding a curve worth having, and knowing
what you are listening to once you have it. The community library can now be filtered
by what a curve *does* rather than by what somebody called it, every card draws its own
shape, and a curve applied from anywhere carries its name with it afterwards.

Underneath that, the app's ten overlapping panels became five screens on a stack, and
three of them that were secretly the same screen became one. Six things the app was
getting wrong are fixed along the way, all of them the kind no amount of new features
would have covered up — and two of them were caught by looking at the window rather than
by anything the test suite could see.

### Fixed

- **The window opened floating, and only on some machines.** On a tiling compositor
  Hub Moon opened as a floating window rather than taking its place in the layout —
  reliably from a packaged build, never from a source checkout, with no window rule
  anywhere to explain it.

  It was the app's own doing. A compositor decides float-versus-tile from the size
  constraints on the surface's *first* commit, and takes that decision once. Slint's
  first layout runs before any data has been pushed into the window, and with empty
  models nothing in the tree stretches horizontally — so the computed maximum width
  collapsed onto the declared minimum and the window mapped saying, in effect, *"I
  cannot be wider than my own minimum"*:

      -> xdg_toplevel#37.set_min_size(912, 700)
      -> xdg_toplevel#37.set_max_size(980, 16777215)   ← a finite maximum WIDTH
      -> wl_surface#35.commit()                        ← mapped; the decision is taken
      …78 ms later…
      -> xdg_toplevel#37.set_max_size(0, 0)            ← "actually, unbounded"

  Slint corrects itself microseconds after the first buffer is attached, which is too
  late. The frozen bundle and the source tree measure text slightly differently, land
  on opposite sides of that race, and so disagreed about a window neither of them was
  describing correctly. `MainWindow` now declares its own unbounded maximum, so the
  first commit carries the truth and there is no race to lose.

- **Running the test suite rewrote your own settings.** `SETTINGS_PATH` was built at
  import time from `mc.config_dir()`, so it was fixed before any test could redirect
  it — and the tests that redirect the config directory did not redirect this. Every
  `save_settings` in the suite therefore wrote to the real settings file of whoever ran
  it, which is how a test run flipped a developer's update channel and community
  filters underneath them. The path is resolved per call now, and a test asserts it
  follows the config directory.

- **An empty list collapsed the sheet it was in.** Open Saved profiles with nothing
  saved, or search the community library for something that matches nothing, and the
  list area shrank to zero: the "nothing here yet" message was clipped away unread and
  the footer rode up under the search box with a third of the panel left blank below
  it.

  A `Flickable` reports its viewport as its own height preference, and an empty
  viewport is zero tall. That zero propagated upward as the *maximum* height of the
  pane meant to stretch, so the one child that was supposed to fill the sheet could
  not grow — and `clip: true` hid the evidence. The Flickable now fills the space it is
  given, with `viewport-height` describing only what scrolls inside it. Fixed in all
  four sheets that use the pattern: profiles, community, headphones and devices.

- **Notices stayed on screen forever.** `toast()` set the text and nothing ever
  cleared it, so "Written to flash." sat over the interface until it was clicked or
  something else replaced it. They now clear themselves after five seconds, ten for
  errors, and a notice that has already been replaced can no longer be cleared out
  from under its successor by an expiry arriving late.

- **The DAC's own EQ profiles were being shown as your curve.** A DAWN PRO2 has five
  built-in profiles and one custom one — the app writes the custom one, the volume buttons
  cycle all of them. Cycle to a built-in and Hub Moon read *that* profile and drew it as
  the curve you were editing, under whatever name you had last applied, with "(edited)"
  after it. Nudge one band and the custom profile was selected instead, so the other seven
  bands snapped back to whatever it held, and the curve on screen turned out never to have
  been one the app could edit.

  The profile row now says which profile is playing and whether it is yours, and offers
  "my curve" to get back — which is a rewrite rather than a profile change, because the
  custom profile has no index that selects it. Its index is not hardcoded either: it is
  learned by watching what the device reports right after this app writes, which costs
  nothing because that read-back already happened, and remembered per product so the
  answer is there the first time you look rather than after you edit.

  What "my curve" writes is what this machine last recorded applying — *not* the bands on
  screen, which while a built-in is playing are the built-in's. The first version of this
  wrote those, and put −6 dB at 200 Hz into a custom profile as though it were the user's
  own tuning.

- **The profile stepper reported positions the DAC had refused.** It counted its own
  clicks. The device accepts 0–4 and silently ignores anything above, so stepping up from
  9 showed 10 while the DAC stayed put — and stepping down to 4 and back showed 5, 6, 7
  from a device that had never left 4, with no way back to the custom profile at all. The
  number shown is now the one read back from the device, and a refused step marks nothing
  as unsaved.

- **A button could be wired to nothing and nobody would know.** A `callback` the interface
  declares with no Python handler is an error in neither language: the control is built,
  it is clickable, it hovers, it does nothing. `tests/test_wiring.py` checks all 79 of
  them.

- **The community library was slow to scroll, and slow just to sit there.** Measured with
  `SLINT_DEBUG_PERFORMANCE`: **116 fps** with an empty list, **54** with four hundred
  community cards, and **49 while scrolling** with dips to 15. Nine cards fit on screen.

  A `for` inside a Flickable builds every row, so four hundred cards at roughly fifteen
  elements each is six thousand elements laid out on every frame whether or not anyone
  can see them. The row *slots* are still real — a bare rectangle each, positioned at a
  fixed pitch, which is also what makes the scroll extent exact rather than measured —
  but the card inside a slot is now built only when the slot is within a viewport of the
  screen. **76 fps sitting still and 74 while scrolling**, with the worst dip up from 15
  to 21. All four library tabs get it, so a long list of saved profiles behaves too.

  Slint's own `ListView` does this properly and lives in `std-widgets`, which this
  interface deliberately imports none of.

  Four numbers now have to agree — each list's pitch, its slot spacing, its card's height
  and where its count comes from — and no behavioural test would notice them drifting,
  because the rows would all still be there and all still correct. `tests/test_lazyrows.py`
  reads them out of the interface file instead.

- **Thumbnails were drawn on the main thread.** Each community and headphone row carries
  its curve as a little SVG path, and at 3.1 ms a curve that was 25 ms of dead frames
  every time a chunk of eight arrived — fifty times over while a page filled in. It is
  arithmetic over plain floats with nothing device- or view-shaped about it, so it happens
  on the thread that fetched the curve now, and what arrives is a finished path.

- **A resize could have crashed the window.** The header's "is this window narrow"
  property was bound to the window's width, and the header's own subtitle changes length
  on that property — so the window's width depended on a text whose width depended on
  the window. Slint reports it as a binding loop that "may cause panic at runtime". The
  test suite cannot see a compiler warning; the app is built from the interface file
  every run and nothing was reading what the build said. It is assigned from a `changed`
  handler now, which runs after the width settles.

- **The build baked in icons nothing draws.** The icon generator collected every
  snake_case string literal in `bridge.py` and kept the ones that happened to share a
  name with a Material Symbols icon. That was near enough when there were few strings;
  this release added `"settings"`, `"source"`, `"clear"`, `"verified"` and eight more
  that mean nothing of the kind, and each one silently baked about a kilobyte of unused
  path data into every build. It reads the preset table as the table it is now.

### Added

- **One library instead of three panels that were the same panel.** Saved profiles, the
  community index and the headphone corrections were three separate full-screen sheets,
  and each of them was the same screen: a searchable list of curves, a preview, apply.
  They are one **Library** now with the source as a tab — Saved · Recent · Community ·
  Headphones, what is yours first and then everybody else's, with the count on each tab.

  The buttons did not merge. The headphones button still opens the headphones tab,
  because landing somewhere you did not ask for — one click from where you did — is not
  an improvement, and pressing the button you came in by still closes the sheet. What
  changed is that the other three are a tab away rather than a close and a reopen.

  Writing the chrome once turned up three things it had been hiding. The search box
  existed three times and one copy had lost its placeholder text entirely. The
  headphones placeholder said "search 6,015 headphones" against a catalogue that has
  8,827 in it today — a count written into the interface drifts the moment AutoEQ
  publishes anything, so it now comes from the data, in the line beside the title. And
  the recent-curves list, which had been squeezed into a 150-pixel strip above the saved
  ones, is a tab with the whole pane.

  Nothing is fetched for a tab nobody has opened. The community index is 5.4 MB and the
  catalogue is 8,827 models; loading both to show one would have made the unified screen
  slower than the three it replaced.

- **Searching the community library matches what a curve does.** Type "bass" and you
  get the bass-boosted curves as well as the ones with "bass" in the title. This matters
  more than it sounds: most of that library is titled in Chinese — `低频增强`, `改善齿音`
  — and a substring search over the title is no help at all to somebody who cannot type
  it. A row whose curve has not been fetched yet has one fewer field to match on, the
  same as one with no description; it is not excluded for being unclassified.

- **Escape goes back, not out.** Every full-screen panel used to carry its own
  open/closed flag, and every one of them had to remember to switch off all the others
  by hand — 44 assignments across `bridge.py`, eight of `settings_open = False` alone.
  There is one stack now (`gui/nav.py`): opening a screen pushes it, the view draws the
  top of the stack and nothing else, and two panels cannot both be up however the app
  got there.

  It was meant to be invisible and is not quite. A screen opened from another one shows
  a back arrow where the close cross was, and one step back returns to where it was
  opened from — so the release notes reached from Settings land back in Settings, and
  the supported-device list does too. That one pair used to be faked with a flag that
  reopened Settings by hand; nothing else could be faked, which is why nothing else did
  it. A test compares the screen names in the interface file against the ones the app
  knows, because a string property is the one kind that can carry a typo with no error
  at all — just a panel that never opens.

- **Curves remember where they came from.** A chip under the presets says what is on
  the device — "Community · aira 咏叹调", "AutoEQ · HD 600" — and says "(edited)" the
  moment a band moves. Through 1.2.0 bands were anonymous: once written, a community
  config, an AutoEQ fit and a curve drawn by hand were byte-identical and the app could
  not tell you which one you were hearing.

  The DAC holds the bands but has nowhere to put a name, so the name lives on this
  machine and is re-attached by fingerprint — a short hash over each band's type,
  frequency, gain and Q, rounded exactly as far as the device round-trips them, because
  anything finer means every reconnect claims you edited something you did not. A
  disabled band contributes only the fact that it is disabled: its leftover frequency
  and gain are never written to the device and must not make two identical curves hash
  apart. When nothing is known, nothing is claimed — an unrecorded curve is *unknown*,
  not "manual".

- **A recent list, and a way back.** The library has a **Recent** tab holding what you
  have applied lately: click one to put it back, or "keep" to promote it to a permanent
  profile. The bands travel in the entry, so going back needs no network and works on a
  config that has since been deleted from the library. It is capped at twenty and
  deduplicated by fingerprint, so flipping between two tunings leaves two rows rather
  than twenty — and it is deliberately its own tab rather than merged into Saved: this
  list rolls over by itself, and that one is only ever written by you.

- **The headphones tab draws its corrections, and says whose they are.** Searching for
  one headphone returns a dozen rows with an identical name — twelve measurements of a
  Sennheiser HD 600 by twelve people on nine rigs — and until now the only thing
  telling them apart was a line of small grey text under a bold model name they all
  shared. The loud half of every row was the half they agreed on.

  Each row now draws its own correction, so two measurements of the same headphone can
  be compared before either is applied rather than by applying both in turn, and names
  its measurer in the accent colour with the shape the curve works out to. Above the
  list, a chip per measurer with a count: oratory1990, crinacle, Rtings and the rest.
  Searching harder cannot narrow twelve rows that share a name — picking a measurer is
  the only filter that can.

  Profiles are fetched in chunks and cached to disk exactly as the community curves
  are, so this costs one fetch per headphone, ever.

- **The community library can be filtered by what a curve actually does.** Nine chips
  — Neutral, Bass boost, V-shaped, Warm, Treble-tamed, Bright, Bass-cut, Other — each
  carrying a count, none of which is derived from anything an author typed.

  It cannot be. Of the 9,243 rows a DAWN PRO2 query returns, **25 carry a tag**;
  titles are things like `"V2 final"` and descriptions are free text with hand-drawn
  frequency tables in them. So the label comes from the curve: each preset's eight
  bands are summed into a response, averaged over five regions, and read as a tilt
  *against the mids* rather than against zero — because judged absolutely, a third of
  the real library came out "Bright" simply for lifting the presence region a little
  while doing something else entirely, and a label that catches a third of everything
  has said nothing. A curve that fits no rule is "Other" rather than being forced into
  the nearest bucket.

  `tools/classify-index.py` prints the histogram over the real cached library, which
  is how the thresholds were chosen rather than guessed: no bucket now holds more than
  20% of it and "Other" holds 11%.

- **Every community card draws its own curve.** A shelf of shapes can be read at a
  glance in a way a shelf of titles cannot. They arrive a chunk at a time as the
  prefetch classifies them, filled in under the reader rather than rebuilt around
  them, and the space is reserved from the start so nothing moves when one lands.

  Curve files are cached permanently, and that is not a shortcut: a `file` ref is
  content-addressed, so editing a preset publishes a new one and the index points
  somewhere else. A cached curve can never go stale, only become unused. The whole of
  a DAWN PRO2's own uploads is about 750 KB.

- **The library knows which presets are for your DAC.** The server pools by
  `sharedConfigGroupId`, so a DAWN PRO2 query answers with the whole family: 9,243
  rows, of which 1,485 are DAWN PRO2 uploads and 5,518 belong to a FreeDSP Pro. Each
  row carries both `productuuid` (who it was uploaded for) and `productUuid` (what we
  asked about), so telling them apart costs one comparison and no extra request. On by
  default, with the whole family one click away, and the header says what is being
  hidden either way — a filter that silently drops five sixths of a library is
  indistinguishable from a library that is nearly empty.

- **The library can be ordered.** Most liked, most downloaded, or best rated, where a
  lone five-star rating cannot outrank a preset with eighty. Previously the order was
  fixed and unwritable. There is deliberately no "newest": no row in the index carries
  a timestamp, and inventing one out of the array's own sequence would be a label that
  lies.

- **Motion is one system with an off switch.** `theme.slint` grew a `motion` scale that
  every duration is multiplied by, and Appearance grew a Full / Reduced / Off control
  for it. Off is genuinely off — a duration of zero animates nothing — and nothing the
  app does depends on an animation finishing. It has to be a setting rather than a
  system preference because Slint cannot read the desktop's reduced-motion flag, and
  animation somebody cannot switch off is an accessibility problem rather than a matter
  of taste.

  Along the way every remaining hand-written value went: 37 easings and the last stray
  520ms are now tokens, the two curves are named for what they are for (`ease-state` for
  something changing where it already is, `ease-enter` for something arriving), and a
  test greps the interface file and fails on any animation carrying its own number.
  1.2.0 had to fix seventeen animations that had drifted off the theme's timing; they
  drifted because nothing complained.

- **Sheets arrive instead of appearing.** Every panel — settings, community, profiles,
  headphones, help, devices — used to exist between one frame and the next, because
  `if open: Sheet {}` puts the whole thing on screen at once. Each now fades in over
  `Theme.screen`. Closing stays immediate: `if` destroys the element, and keeping a
  heavy sheet alive purely to fade it out is a cost paid on every frame it is open.

- **About is about the product now, not only about the build.** The app's own mark and
  name at the size an identity is read at, the version as a chip beside it, then three
  cards saying what Hub Moon actually does — eight bands on the DAC's own DSP, the
  community library, AutoEQ corrections — followed by what is plugged in and a way
  through to the supported-device list. The bug-report block is still there, under a
  heading that says why it exists rather than as the first thing on the page, and the
  credits are named: AutoEQ and its measurers, Moondrop's library as read-only and
  unaffiliated, Slint, and this project's own MIT licence.

- **The last paragraph of About could not be reached.** With the dialog no longer
  resizing itself, About scrolls — and its scroll area stopped short, so "Credits and
  licence" was the last thing on the page and the credits themselves were unreachable.

  A `Text` that wraps only knows how tall it is once it knows how wide it is, and the
  panel's width is not resolved at the moment its Flickable asks the content how tall
  it would like to be. Every paragraph therefore measured as a single line and the
  viewport came up around 300px short. The panel's prose now carries an explicit width
  — the sheet, less its padding, the rail, the rule and the gap either side of it.

- **A narrow window cut the header and footer off.** With a DAC connected the header
  wants a device chip, a firmware chip, three named buttons and three icons; below
  about a thousand pixels the last of them ran off the right edge — "how to tune"
  sliced through the middle of a word, the settings and refresh icons gone altogether.
  The footer did the same to "save to flash".

  Below 1100px the header and footer keep their buttons and drop their labels, and the
  subtitle shortens. It is not the window's declared minimum, deliberately: a tiling
  compositor hands the app whatever the tile is — 939px here, less than the 980 it asks
  for — so being too narrow is a state the layout has to survive rather than one it can
  refuse. "compare" keeps its label at every width, because that one says whether you
  are listening to the curve or bypassing it.

- **The settings dialog stopped resizing itself.** Its height was each tab's own
  content height, so it grew and shrank under the pointer as you moved down the rail —
  some 300px between Appearance and Window. It is one height for every tab now, and the
  panel scrolls if a page needs more. A settings window that will not hold still while
  you read it is worse than one with room to spare at the bottom.

- **Settings has two new pages, and the settings file has a version.** *Window* holds
  open-fullscreen and the device watch; *Library* holds curve fetching and the recent
  list. Every switch is one the app was already deciding on its own — the point of the
  pages is to hand those decisions back, not to invent preferences.

  `settings.json` now carries a `schema`, which it never has. `load_settings`
  whitelists keys and silently drops the rest, so until now an unrecognised file and an
  old one were indistinguishable and no migration could ever have been written. 2.0 is
  the release that can afford the break, so it is the release that takes it.

  Opening fullscreen is off by default everywhere: on a tiling compositor the window
  already fills its tile, and true fullscreen additionally covers the bar. It is also
  not declared in the interface file — `full-screen: true` there is read before the
  window exists and does nothing at all, verified on a bare Slint window as well as on
  ours. The property only takes effect when it *changes*, so the app sets it once the
  event loop is up.

- **The app notices a DAC being plugged in or pulled out.** It used to look exactly
  once, at launch: a device connected a second later stayed invisible until somebody
  discovered that the device chip is a button, and one unplugged mid-session was
  noticed only by the next operation failing. A two-second watch now answers both.

  It costs one `hid.enumerate()` — measured at 0.75 ms — on the worker thread that
  already owns the device, it never opens the hidraw, it posts nothing unless the
  answer changed, and it skips itself entirely whenever that worker has real work
  queued, so a poll can never land in the middle of a drag. An unplug drops the stale
  handle before announcing itself, and does *not* raise the welcome screen the way a
  failed search at startup does — pulling a cable out is not a question about what
  hardware you own.

## [1.2.0] - 2026-08-17

**Stable.** Five betas, and the one that mattered was invisible to every test in the suite:
a frozen build could not start a single system program, so most of what a packaged release
does had been broken since the first one. That is fixed, CI now runs the binary it is about
to ship, and the interface stopped pretending 1.6 seconds of entrance animation was polish.

> **Updating from 1.1.0 or any 1.2.0 beta on a `.deb`, `.rpm` or Arch install needs one
> manual hop.** Those builds report themselves as a loose tarball, so their updater tries to
> overwrite `/opt/hub-moon` and stops. Install this one with your package manager once, and
> every update after it works from the button.

### Changed

- **Every animation now runs on the theme's own timing.** Testers reported the interface
  lagging — scrolling, the logo, the What's New panel — on machines with nothing else wrong
  with them, and the cause was not performance. `theme.slint` defines `quick: 110ms` and
  `settle: 220ms`, and seventeen animations bypassed both with hand-written values from 280ms
  to **1150ms**, layered on top of entrance delays of up to **1200ms**.

  The worst element on a freshly opened page did not finish appearing until roughly **1.66
  seconds** after it opened. That is not a frame-rate problem and no hardware fixes it — which
  is exactly why it reproduced on every tester's machine. It reads as lag because functionally
  it *is* lag: the interface is not ready when you are.

  Timing now comes from the tokens in every case, the twelve flat entrance delays are gone,
  and the three cascading list staggers run on a new `Theme.stagger` of 14ms a row rather than
  70–110ms. Worst case goes from ~1660ms to ~220ms.

  The staggered reveal was deliberate, and losing it is the cost. A page that assembles itself
  over a second and a half looks considered exactly once — the first time — and looks broken
  every time after that, which is the view a tester has and a designer does not.

### Added

- **The bundle smoke test now runs on macOS and Windows too.** b5 added it to the
  Linux build only, which left unchecked the two platforms it had the most to say
  about. macOS especially: the library-path bug broke `hdiutil`, `ditto`, `xattr` and
  `codesign` there, which is the whole of the `.dmg` updater, and nothing in CI would
  have noticed that fix regressing. The macOS check runs *after* the ad-hoc signature
  rather than before, so it also catches a `codesign --deep` that breaks the app —
  `--verify` is allowed to fail there without stopping the build, so nothing else
  would.

  The Windows bundle is windowed (`console=False`), so it has no console attached and
  prints into the void. `--selftest` therefore takes an optional file to write its
  report to, which is the one channel all three platforms share; the checks that read
  stdout stay on the two platforms that have one, and `--selftest` has already proved
  the CLI runs by the time they are reached.

  The linker check learned dyld's wording as well as glibc's. Matching only
  `not found (required by` meant it passed unconditionally on macOS — a check that
  could not fail on the platform it most needed to catch.

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
