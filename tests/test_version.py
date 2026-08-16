"""One version string, and proof that nothing has quietly grown a second copy.

This is the test the 1.0.0 release needed: `hub-moon.spec` carried a hardcoded
`0.2.0` in the macOS Info.plist for two releases, so Get Info and the About panel
reported a version two behind the app. Nothing compared them, so nothing noticed.
"""
import os
import re

import pytest

import moondrop_control as mc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_version_is_a_sane_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-.]?(?:a|b|rc|alpha|beta|pre)\.?\d*)?",
                        mc.__version__), mc.__version__


def test_pyproject_reads_the_version_rather_than_repeating_it():
    text = read("pyproject.toml")
    assert 'dynamic = ["version"]' in text
    assert 'attr = "moondrop_control.__version__"' in text
    assert not re.search(r'^version\s*=\s*"', text, re.M), \
        "pyproject must not carry its own copy of the version"


def test_the_pyinstaller_spec_finds_the_same_version():
    """The spec parses moondrop_control.py rather than importing it — importing would
    need hidapi on the build machine. Its `_version()` is lifted out and *run* here,
    rather than its regex being copied, so a rename that breaks the macOS bundle
    fails this test instead of shipping a mislabelled .app.
    """
    import ast

    spec_src = read("packaging", "hub-moon.spec")
    assert "CFBundleShortVersionString" in spec_src
    assert '"0.2.0"' not in spec_src, "the hardcoded macOS version is back"

    tree = ast.parse(spec_src)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_version"), None)
    assert fn is not None, "hub-moon.spec no longer defines _version()"

    ns = {"os": os, "re": re, "repo": ROOT}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "hub-moon.spec", "exec"), ns)
    assert ns["_version"]() == mc.__version__


@pytest.mark.parametrize("path,pattern", [
    ("flake.nix", r'version\s*=\s*"([^"]+)"'),
    ("packaging/nfpm.yaml", r'^version:\s*(\S+)'),
    ("packaging/PKGBUILD", r'^pkgver=(\S+)'),
    ("packaging/PKGBUILD.local", r'^pkgver=(\S+)'),
    ("packaging/hub-moon.iss", r'#define AppVersion "([^"]+)"'),
])
def test_packaging_files_agree(path, pattern):
    """These are given the version on the command line by CI, but the checked-in
    default is what someone building by hand gets — so it has to be right too."""
    got = re.search(pattern, read(*path.split("/")), re.M)
    assert got, "no version found in %s" % path
    assert got.group(1) == mc.__version__, \
        "%s says %s, moondrop_control says %s" % (path, got.group(1), mc.__version__)


def test_the_changelog_documents_this_version():
    head = read("CHANGELOG.md")
    assert re.search(r"^## \[%s\]" % re.escape(mc.__version__), head, re.M), \
        "CHANGELOG.md has no entry for %s" % mc.__version__


def test_the_user_agent_carries_the_version():
    assert mc.__version__ in mc.HUB_UA


def test_directories_differ_per_platform_but_are_absolute():
    for fn in (mc.config_dir, mc.cache_dir, mc.log_dir):
        got = fn()
        assert os.path.isabs(got), "%s returned %r" % (fn.__name__, got)
        assert "~" not in got, "%s did not expand the home directory" % fn.__name__


def test_config_and_cache_are_not_the_same_place():
    """Deleting the cache must never take the settings with it."""
    assert os.path.abspath(mc.config_dir()) != os.path.abspath(mc.cache_dir())


@pytest.mark.parametrize("plat,expect", [
    ("win32", "HubMoon"),
    ("darwin", "Application Support"),
    ("linux", "hub-moon"),
])
def test_each_platform_gets_its_own_convention(monkeypatch, plat, expect):
    monkeypatch.setattr(mc.sys, "platform", plat)
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert expect in mc.config_dir()
