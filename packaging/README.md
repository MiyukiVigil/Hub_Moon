# Packaging Hub Moon

Everything here wraps **one** thing: the `pyproject.toml` at the repo root, which
turns Hub Moon into an installable package with two entry points —

| command | what it is |
|---|---|
| `hub-moon` | the full CLI (and `hub-moon --gui`) |
| `hub-moon-gui` | straight to the desktop GUI (windowed) |

The QML tree ships as package data, and `gui/app.py` finds it whether the app runs
from source, an installed wheel, or a frozen bundle. So there are two routes:

- **distro-native** (Arch, Nix) — depend on the system PySide6. Lean.
- **bundled** (Windows `.exe`, macOS `.dmg`, `.deb`/`.rpm`, AppImage) — PyInstaller
  freezes PySide6 + Qt + QML in, so there's no per-distro PySide6 dependency to chase.

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
| `build-macos.yml` | macos-14 + macos-13 | `.dmg` for Apple Silicon **and** Intel |

Each needs `permissions: contents: write` (set in the workflow) so the release
step can upload — the default `GITHUB_TOKEN` is read-only.

---

## Plain pip (any OS)

```bash
pip install .            # CLI only
pip install ".[gui]"     # CLI + GUI (pulls in PySide6)
hub-moon-gui
```

## Arch  ✅ verified with makepkg

Two recipes:

```bash
# now — build + install from your working copy (no tag needed)
cd packaging && makepkg -si -p PKGBUILD.local

# release — after you push a v0.2.0 tag to GitHub
cd packaging && updpkgsums && makepkg -si          # updpkgsums fills the source digest
```

Both depend on `python-hidapi` and `pyside6` from the repos and install the udev rule,
desktop file and icon. `PKGBUILD.local` stages a clean copy of the repo (via rsync) so
the build never touches your working tree. The release `PKGBUILD` pulls the source from
the GitHub tag and takes the udev/desktop/icon from the three files kept **next to the
PKGBUILD** (standard AUR layout) — so they don't need to be inside the source tarball.

> The 404 you'll get from plain `makepkg` before tagging is expected — it's trying to
> download `v0.2.0.tar.gz`, which doesn't exist until you tag. Use `PKGBUILD.local`
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
iscc /DAppVersion=0.2.0 packaging\hub-moon.iss            # → dist\HubMoon-Setup-0.2.0.exe
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

nfpm makes both from one config, wrapping the PyInstaller bundle (so no PySide6
dependency to name per distro). `build-linux.yml` does this automatically; by hand:

```bash
pyinstaller packaging/hub-moon.spec                       # build the bundle first
nfpm pkg --packager deb -f packaging/nfpm.yaml -t dist/   # → dist/hub-moon_0.2.0-1_amd64.deb
nfpm pkg --packager rpm -f packaging/nfpm.yaml -t dist/   # → dist/hub-moon-0.2.0-1.x86_64.rpm
```
`nfpm` is a single Go binary — https://nfpm.goreleaser.com. Installs to `/opt/hub-moon`
with a `/usr/bin/hub-moon-gui` symlink; `postinstall.sh` reloads udev.

## AppImage — `build-appimage.sh`  ✅ built + launched on Linux

A single portable file that runs on any modern Linux, no install:

```bash
pyinstaller packaging/hub-moon.spec                       # build the bundle first
packaging/build-appimage.sh 0.2.0                         # → dist/HubMoon-0.2.0-x86_64.AppImage
```
Needs `appimagetool` on PATH (and `rsvg-convert`/ImageMagick for the icon). Also
run automatically by `build-linux.yml`.

## Nix — `flake.nix`  (templated — needs Nix)

Distro-native route (depends on nixpkgs `pyside6`/`hidapi`):

```bash
nix build          # result/bin/hub-moon-gui
nix run            # launches the GUI
```
NixOS users can `imports = [ hub-moon.nixosModules.default ]` to get the app **and**
the udev rule system-wide. Qt wrapping is via `qt6.wrapQtAppsHook`; if QML plugins
aren't found at runtime, that hook is the knob to check.

## Flatpak (optional, not scripted here)

- **Flatpak** — cleanest sandboxed cross-distro GUI, but hidraw needs
  `--device=all` and the **host** still needs the udev rule. Say the word and I'll
  add a manifest.

---

### What's verified vs. templated

Built and run on this machine (Arch, x86_64):

- **wheel** — installed into a clean venv, GUI launched from outside the repo.
- **Arch package** — `makepkg`, layout inspected.
- **PyInstaller bundle** — built and launched; QML resolves from the frozen bundle,
  including the connect / config / preview screens.
- **AppImage** — built with `build-appimage.sh` and launched (stays up, no QML errors).
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
