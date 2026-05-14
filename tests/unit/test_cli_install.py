"""Tests for quicksand.cli.install (pip-backed installer)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from quicksand.cli import install as install_mod
from quicksand.cli.install import (
    _ensure_images,
    _install_packages,
    _parse_extra,
    _resolve_packages,
    _run_pip_install,
    install,
)


class TestParseExtra:
    def test_bare_name(self):
        assert _parse_extra("ubuntu") == ("ubuntu", None)

    def test_pinned_version(self):
        assert _parse_extra("ubuntu@0.4.0") == ("ubuntu", "0.4.0")

    def test_first_at_only(self):
        # Versions can't contain "@", so split once.
        assert _parse_extra("pkg@1.0@beta") == ("pkg", "1.0@beta")


class TestResolvePackages:
    def test_alias_expands(self):
        assert _resolve_packages("ubuntu") == ["quicksand-ubuntu"]

    def test_dev_alias_expands_to_multiple(self):
        assert _resolve_packages("dev") == [
            "quicksand-image-tools",
            "quicksand-overlay-scaffold",
            "quicksand-base-scaffold",
        ]

    def test_unknown_is_passthrough(self):
        # Treated as a literal PyPI name — pip will resolve or fail.
        assert _resolve_packages("some-random-pkg") == ["some-random-pkg"]


class _DummyCompleted:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


class TestRunPipInstall:
    def test_builds_pip_argv(self):
        captured: dict = {}

        def fake_run(argv, *_args, **_kwargs):
            captured["argv"] = argv
            return _DummyCompleted(0)

        with patch("quicksand.cli.install.subprocess.run", side_effect=fake_run):
            rc = _run_pip_install(["quicksand-qemu", "quicksand-cua"], {})

        assert rc == 0
        assert captured["argv"][:7] == [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--upgrade-strategy",
            "only-if-needed",
        ]
        # Package args appear unpinned and in order.
        assert captured["argv"][7:] == ["quicksand-qemu", "quicksand-cua"]

    def test_applies_version_pins(self):
        captured: dict = {}

        def fake_run(argv, *_args, **_kwargs):
            captured["argv"] = argv
            return _DummyCompleted(0)

        with patch("quicksand.cli.install.subprocess.run", side_effect=fake_run):
            _run_pip_install(
                ["quicksand-qemu", "quicksand-ubuntu"],
                {"quicksand-qemu": "0.5.9", "quicksand-ubuntu": None},
            )

        assert "quicksand-qemu==0.5.9" in captured["argv"]
        assert "quicksand-ubuntu" in captured["argv"]
        assert "quicksand-ubuntu==None" not in captured["argv"]


@dataclass
class _FakeDist:
    name: str


class _FakeEntryPoint:
    def __init__(self, dist_name: str, provider):
        self.dist = _FakeDist(dist_name)
        self._provider = provider

    def load(self):
        return self._provider


class _FakeProvider:
    def __init__(self, images_dir: Path) -> None:
        self.images_dir = images_dir


class TestEnsureImages:
    def test_skips_packages_not_in_request(self, tmp_path: Path):
        images = tmp_path / "ubuntu-images"
        images.mkdir()
        eps = [_FakeEntryPoint("quicksand-ubuntu", _FakeProvider(images))]

        with (
            patch("quicksand.cli.install.entry_points", return_value=eps),
            patch("quicksand_core._auto_install.auto_install_images") as auto,
        ):
            _ensure_images(packages=["quicksand-qemu"], arch=None)

        auto.assert_not_called()

    def test_skips_when_manifest_present_and_no_arch(self, tmp_path: Path):
        images = tmp_path / "ubuntu-images"
        images.mkdir()
        (images / "manifest.json").write_text("{}")
        eps = [_FakeEntryPoint("quicksand-ubuntu", _FakeProvider(images))]

        with (
            patch("quicksand.cli.install.entry_points", return_value=eps),
            patch("quicksand_core._auto_install.auto_install_images") as auto,
        ):
            _ensure_images(packages=["quicksand-ubuntu"], arch=None)

        auto.assert_not_called()

    def test_fetches_when_manifest_missing(self, tmp_path: Path):
        images = tmp_path / "ubuntu-images"
        images.mkdir()  # empty — pure-wheel install case
        eps = [_FakeEntryPoint("quicksand-ubuntu", _FakeProvider(images))]

        with (
            patch("quicksand.cli.install.entry_points", return_value=eps),
            patch("quicksand_core._auto_install.auto_install_images") as auto,
        ):
            _ensure_images(packages=["quicksand-ubuntu"], arch=None)

        auto.assert_called_once_with("quicksand-ubuntu", images, arch=None)

    def test_fetches_for_cross_arch_even_with_manifest(self, tmp_path: Path):
        # --arch forces a refetch so we can overlay images for a different arch.
        images = tmp_path / "ubuntu-images"
        images.mkdir()
        (images / "manifest.json").write_text("{}")
        eps = [_FakeEntryPoint("quicksand-ubuntu", _FakeProvider(images))]

        with (
            patch("quicksand.cli.install.entry_points", return_value=eps),
            patch("quicksand_core._auto_install.auto_install_images") as auto,
        ):
            _ensure_images(packages=["quicksand-ubuntu"], arch="amd64")

        auto.assert_called_once_with("quicksand-ubuntu", images, arch="amd64")


class TestInstall:
    def test_pip_failure_raises(self):
        with (
            patch(
                "quicksand.cli.install.subprocess.run",
                return_value=_DummyCompleted(1),
            ),
            pytest.raises(RuntimeError, match="Failed to install"),
        ):
            install("qemu")

    def test_no_extras_raises(self):
        with pytest.raises(ValueError):
            install()

    def test_install_packages_skips_image_fetch_on_pip_failure(self):
        with (
            patch(
                "quicksand.cli.install.subprocess.run",
                return_value=_DummyCompleted(2),
            ),
            patch.object(install_mod, "_ensure_images") as ensure,
        ):
            rc = _install_packages(["quicksand-ubuntu"], {}, arch=None)

        assert rc == 2
        ensure.assert_not_called()

    def test_install_packages_calls_image_fetch_on_success(self):
        with (
            patch(
                "quicksand.cli.install.subprocess.run",
                return_value=_DummyCompleted(0),
            ),
            patch.object(install_mod, "_ensure_images") as ensure,
        ):
            rc = _install_packages(["quicksand-ubuntu"], {"quicksand-ubuntu": None}, arch="amd64")

        assert rc == 0
        ensure.assert_called_once_with(["quicksand-ubuntu"], arch="amd64")
