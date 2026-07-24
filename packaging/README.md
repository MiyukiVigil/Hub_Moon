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
- **bundled** (Windows `.exe`, `.deb`/`.rpm`, AppImage) — PyInstaller freezes
  PySide6 + Qt + QML in, so there's no per-distro PySide6 dependency to chase.

Two things every package installs besides the code:
- **`70-moondrop.rules`** — the udev rule, or the DAC won't open without `sudo`.
- **`hub-moon.desktop`** + **`hub-moon.svg`** — the launcher and its icon.

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
- **Linux/macOS** → `pyinstaller packaging/hub-moon.spec` gives `dist/hub-moon/`,
  a self-contained folder (the `.deb`/`.rpm` below package the Linux one).

## `.deb` + `.rpm` — `nfpm.yaml`  (templated — needs a Linux build host)

nfpm makes both from one config, wrapping the PyInstaller bundle (so no PySide6
dependency to name per distro):

```bash
pyinstaller packaging/hub-moon.spec                       # build the bundle first
nfpm pkg --packager deb -f packaging/nfpm.yaml            # → hub-moon_0.2.0_amd64.deb
nfpm pkg --packager rpm -f packaging/nfpm.yaml            # → hub-moon-0.2.0.x86_64.rpm
```
`nfpm` is a single Go binary — https://nfpm.goreleaser.com. Installs to `/opt/hub-moon`
with a `/usr/bin/hub-moon-gui` symlink; `postinstall.sh` reloads udev.

## Nix — `flake.nix`  (templated — needs Nix)

Distro-native route (depends on nixpkgs `pyside6`/`hidapi`):

```bash
nix build          # result/bin/hub-moon-gui
nix run            # launches the GUI
```
NixOS users can `imports = [ hub-moon.nixosModules.default ]` to get the app **and**
the udev rule system-wide. Qt wrapping is via `qt6.wrapQtAppsHook`; if QML plugins
aren't found at runtime, that hook is the knob to check.

## AppImage / Flatpak (optional, not scripted here)

- **AppImage** — wrap the PyInstaller `dist/hub-moon/` with `appimagetool` for a
  single portable Linux file.
- **Flatpak** — cleanest sandboxed cross-distro GUI, but hidraw needs
  `--device=all` and the **host** still needs the udev rule. Say the word and I'll
  add a manifest.

---

### What's verified vs. templated

Built and run on this machine (Arch): the **wheel** (installed into a clean venv,
GUI launched from outside the repo), the **Arch package** (`makepkg`, layout
inspected), and the **PyInstaller bundle** (built and launched — QML resolves from
the frozen bundle, including the connect / config / preview screens). The **nfpm**
and **Nix** configs are correct-by-construction but weren't run here (no `nfpm`/`nix`
on this box). The **Windows `.exe` + Inno Setup installer** use the same spec that
built and ran on Linux, driven by the `build-windows.yml` GitHub Action on a
`windows-latest` runner — the same code path with a Windows target. `hub-moon.ico`
was generated from `hub-moon.svg` and ships alongside the spec.

[Inno Setup]: https://jrsoftware.org/isinfo.php
