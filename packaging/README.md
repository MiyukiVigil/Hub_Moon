# Packaging Hub Moon

Everything here wraps **one** thing: the `pyproject.toml` at the repo root, which
turns Hub Moon into an installable package with two entry points —

| command | what it is |
|---|---|
| `hub-moon` | the full CLI (and `hub-moon --gui`) |
| `hub-moon-gui` | straight to the desktop GUI (windowed) |

The `.slint` sources ship as package data, and `gui/app.py` finds them whether the app runs
from source, an installed wheel, or a frozen bundle. So there are two routes:

- **distro-native** (Arch, Nix) — depend on the system slint bindings. Lean.
- **bundled** (Windows `.exe`, macOS `.dmg`, `.deb`/`.rpm`, AppImage) — PyInstaller
  freezes slint in, so there's no per-distro toolkit dependency to chase.

Two things every package installs besides the code:
- **`70-moondrop.rules`** — the udev rule, or the DAC won't open without `sudo`.
- **`hub-moon.desktop`** + **`hub-moon.svg`** — the launcher and its icon.

---

## Automated builds — GitHub Actions

Three workflows in `.github/workflows/` build every installer on the right OS
(PyInstaller can't cross-compile, so each target needs its own runner). **Push a
tag `vX.Y.Z`** and all of them attach their artifacts to that GitHub Release;
**Run workflow** (Actions tab) builds them as downloadable artifacts instead.

| workflow | runner | produces |
|---|---|---|
| `build-windows.yml` | windows-latest | `HubMoon-Setup-<v>.exe` (Inno Setup) + portable zip |
| `build-linux.yml` | ubuntu-22.04 | portable `.tar.gz`, `.AppImage`, `.deb`, `.rpm` |
| `build-macos.yml` | macos-14 | `.dmg` for Apple Silicon |

Each also attaches a `SHA256SUMS-*.txt` for its own assets, hashed on the runner that
built them. Each needs `permissions: contents: write` (set in the workflow) so the
release step can upload — the default `GITHUB_TOKEN` is read-only.

---

## Cutting a release

The version lives in **one** place: `__version__` in `moondrop_control.py`.
`pyproject.toml` reads it with `attr:`, `hub-moon.spec` reads it for the macOS bundle,
the GUI shows it, and the updater compares against it. `nfpm.yaml` and `hub-moon.iss`
are given it on the command line by the workflows. Nothing else may carry a copy —
that is how the `.app` ended up announcing `0.2.0` throughout 1.0.0.

```bash
# 1. bump the one constant, and write the changelog entry
$EDITOR moondrop_control.py CHANGELOG.md

# 2. compile the changelog into the build. `tests/test_release_notes_tool.py` fails
#    if this is skipped, so a version can't ship unable to say what is in it.
python3 tools/build-release-notes.py

# 3. tag it — the three workflows fire on the tag and build the release
git commit -am "v1.1.0" && git tag v1.1.0 && git push origin main --tags

# 4. once all three workflows are green, build the update manifest from the release
python3 tools/build-update-manifest.py v1.1.0

# 5. put it where the app looks: packaging/update.json on main (stable),
#    packaging/update-beta.json on test (beta)
git add packaging/update.json && git commit -m "manifest: 1.1.0" && git push
```

**Step 2 is not optional, and it is not the same thing as step 4.** The manifest's
notes come from the GitHub release body and are shown to somebody deciding whether to
install; `gui/notes.py` is compiled into the build and is what the app shows *after*
updating, or on any build that never came from a release — a `makepkg -si`, a wheel
from a git URL, a beta whose workflows have not finished. The beta channel is mostly
made of those, which is why 1.2.0b1's What's New panel opened empty.

For a **beta**, tag it `v1.2.0-beta.1`, mark the GitHub release as a *pre-release*, and
push the generated `update-beta.json` to the `test` branch. `build-update-manifest.py`
decides which file it writes from the release's pre-release flag, not from a switch, so
a beta cannot be published to the stable channel by mistake.

The app tries the website first and the git branch second, so copying the manifest into
the site (`hubmoon/update.json`) makes the check faster but is not required:

```bash
python3 tools/build-update-manifest.py v1.1.0 --site ../self-website/hubmoon
```

`packaging/update.example.json` documents the format. An asset with no SHA-256 is
**refused** by the app rather than installed, so a manifest whose checksums are missing
is worse than no manifest at all — the generator warns and leaves such assets out.

---

## Plain pip (any OS)

```bash
pip install .            # CLI only
pip install ".[gui]"     # CLI + GUI (pulls in slint)
hub-moon-gui
```

## Arch  ✅ verified with makepkg

Two recipes:

```bash
# now — build + install from your working copy (no tag needed)
cd packaging && makepkg -si -p PKGBUILD.local

# release — after you push a v1.0.0 tag to GitHub
cd packaging && updpkgsums && makepkg -si          # updpkgsums fills the source digest
```

Both depend on `python-hidapi` and `python-slint` from the repos and install the udev rule,
desktop file and icon. `PKGBUILD.local` stages a clean copy of the repo (via rsync) so
the build never touches your working tree. The release `PKGBUILD` pulls the source from
the GitHub tag and takes the udev/desktop/icon from the three files kept **next to the
PKGBUILD** (standard AUR layout) — so they don't need to be inside the source tarball.

> The 404 you'll get from plain `makepkg` before tagging is expected — it's trying to
> download `v1.0.0.tar.gz`, which doesn't exist until you tag. Use `PKGBUILD.local`
> until then.

## Windows `.exe` + installer (and macOS `.app`, bundled Linux) — `hub-moon.spec` / `hub-moon.iss`  ✅ spec verified on Linux

PyInstaller is **not** a cross-compiler — build on the OS you're targeting.

### The easy way: GitHub Actions (no Windows machine needed)

`.github/workflows/build-windows.yml` builds the `.exe` **and** the installer on a
`windows-latest` runner:

- **push a tag** `vX.Y.Z` → the installer + a portable zip are attached to the GitHub Release.
- **Run workflow** (manual, Actions tab) → same assets, as downloadable artifacts.

### Building it by hand on Windows

```powershell
pip install ".[gui]" pyinstaller
pyinstaller packaging\hub-moon.spec                       # → dist\hub-moon\hub-moon.exe (windowed)
iscc /DAppVersion=1.0.0 packaging\hub-moon.iss            # → dist\HubMoon-Setup-1.0.0.exe
```
- `hub-moon.ico` (shipped here, generated from `hub-moon.svg`) is picked up
  automatically as the exe/taskbar/installer icon.
- `hub-moon.iss` is an [Inno Setup] script: installs to *Program Files*, adds a
  Start-menu (and optional desktop) shortcut, and registers an uninstaller. No
  driver step — Moondrop DACs are plain USB HID, which Windows handles natively.
- **Linux** → `pyinstaller packaging/hub-moon.spec` gives `dist/hub-moon/`, a
  self-contained folder (the tarball / AppImage / `.deb` / `.rpm` all wrap it).

## macOS `.app` + `.dmg` — `hub-moon.spec` / `build-macos.yml`  ✅ spec verified on Linux

On macOS the spec's `BUNDLE` step wraps the bundle into `dist/Hub Moon.app` (using
`hub-moon.icns`, generated from the SVG). `build-macos.yml` then ad-hoc signs it and
`hdiutil`-packages a drag-to-Applications `.dmg`, for **both** Apple Silicon and Intel.

```bash
pyinstaller packaging/hub-moon.spec        # on macOS → dist/Hub Moon.app
# then hdiutil create … (see build-macos.yml)
```
**Not notarized** — that needs a paid Apple Developer ID. First launch trips
Gatekeeper ("unidentified developer"); right-click → **Open**, or
`xattr -dr com.apple.quarantine "/Applications/Hub Moon.app"`. Ad-hoc signing is
still done so the arm64 build runs at all.

## `.deb` + `.rpm` — `nfpm.yaml`  ✅ built + inspected on Linux

nfpm makes both from one config, wrapping the PyInstaller bundle (so no toolkit
dependency to name per distro). `build-linux.yml` does this automatically; by hand:

```bash
pyinstaller packaging/hub-moon.spec                       # build the bundle first
nfpm pkg --packager deb -f packaging/nfpm.yaml -t dist/   # → dist/hub-moon_1.0.0-1_amd64.deb
nfpm pkg --packager rpm -f packaging/nfpm.yaml -t dist/   # → dist/hub-moon-1.0.0-1.x86_64.rpm
```
`nfpm` is a single Go binary — https://nfpm.goreleaser.com. Installs to `/opt/hub-moon`
with a `/usr/bin/hub-moon-gui` symlink; `postinstall.sh` reloads udev.

## AppImage — `build-appimage.sh`  ✅ built + launched on Linux

A single portable file that runs on any modern Linux, no install:

```bash
pyinstaller packaging/hub-moon.spec                       # build the bundle first
packaging/build-appimage.sh 1.0.0                         # → dist/HubMoon-1.0.0-x86_64.AppImage
```
Needs `appimagetool` on PATH (and `rsvg-convert`/ImageMagick for the icon). Also
run automatically by `build-linux.yml`.

## Nix — `flake.nix`  (templated — needs Nix)

Distro-native route (depends on nixpkgs `slint`/`hidapi`):

```bash
nix build          # result/bin/hub-moon-gui
nix run            # launches the GUI
```
NixOS users can `imports = [ hub-moon.nixosModules.default ]` to get the app **and**
the udev rule system-wide. There is no toolkit wrapping to do — slint has no plugin path to fix up.

## Flatpak (optional, not scripted here)

- **Flatpak** — cleanest sandboxed cross-distro GUI, but hidraw needs
  `--device=all` and the **host** still needs the udev rule. Say the word and I'll
  add a manifest.

---

### What's verified vs. templated

Built and run on this machine (Arch, x86_64):

- **wheel** — installed into a clean venv, GUI launched from outside the repo.
- **Arch package** — `makepkg`, layout inspected.
- **PyInstaller bundle** — built and launched; the .slint sources resolve from the frozen bundle,
  including the connect / config / preview screens.
- **AppImage** — built with `build-appimage.sh` and launched (stays up, no load errors).
- **`.deb`** — built with nfpm and its payload inspected (`/opt/hub-moon/hub-moon`,
  the `/usr/bin/hub-moon-gui` symlink, udev rule, desktop file, icon, `postinst`).
- **`.rpm`** — built with nfpm (same config; not installed here — no `rpm` on this box).

Correct-by-construction but **not run here**: **Nix** (no `nix` on this box), and
the **Windows** and **macOS** installers — those need their own OS. They use the
same `hub-moon.spec` that built and ran on Linux, so it's the same code path with a
different target, driven by the `build-windows.yml` / `build-macos.yml` runners.
`hub-moon.ico` (Windows) and `hub-moon.icns` (macOS) were generated from
`hub-moon.svg` and ship alongside the spec. The macOS build is **unsigned/not
notarized** — Gatekeeper needs a right-click → Open on first launch.

[Inno Setup]: https://jrsoftware.org/isinfo.php
