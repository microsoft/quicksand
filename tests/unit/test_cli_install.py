"""Tests for quicksand.cli.install (pip-backed installer)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from packaging.requirements import Requirement
from quicksand.cli.install import (
    _collect,
    _expand_requirement,
    _install_packages,
    _run_pip_install,
    install,
)


class TestExpandRequirement:
    def test_bare_alias(self):
        out = _expand_requirement("ubuntu")
        assert [str(r) for r in out] == ["quicksand-ubuntu"]

    def test_alias_with_specifier(self):
        out = _expand_requirement("qemu==0.5.9")
        assert [str(r) for r in out] == ["quicksand-qemu==0.5.9"]

    def test_range_specifier(self):
        out = _expand_requirement("ubuntu>=0.4,<0.5")
        assert len(out) == 1
        assert out[0].name == "quicksand-ubuntu"
        assert "0.4" in str(out[0].specifier) and "0.5" in str(out[0].specifier)

    def test_literal_pypi_name_unchanged(self):
        out = _expand_requirement("quicksand-qemu==0.5.9")
        assert [str(r) for r in out] == ["quicksand-qemu==0.5.9"]

    def test_dev_alias_expands_to_multiple(self):
        out = _expand_requirement("dev")
        assert {r.name for r in out} == {
            "quicksand-image-tools",
            "quicksand-overlay-scaffold",
            "quicksand-base-scaffold",
        }

    def test_dev_alias_propagates_specifier(self):
        out = _expand_requirement("dev>=0.3")
        for r in out:
            assert ">=0.3" in str(r.specifier)


class TestCollect:
    def test_dedupes_after_alias_expansion(self):
        # ``qemu`` and ``quicksand-qemu`` resolve to the same requirement.
        out = _collect(["qemu", "quicksand-qemu"])
        assert [str(r) for r in out] == ["quicksand-qemu"]

    def test_preserves_order(self):
        out = _collect(["ubuntu", "qemu"])
        assert [r.name for r in out] == ["quicksand-ubuntu", "quicksand-qemu"]


class _DummyCompleted:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


class TestRunPipInstall:
    def test_builds_pip_argv(self):
        captured: dict = {}

        def fake_run(argv, *_args, **_kwargs):
            captured["argv"] = argv
            return _DummyCompleted(0)

        reqs = [Requirement("quicksand-qemu"), Requirement("quicksand-cua")]
        with patch("quicksand.cli.install.subprocess.run", side_effect=fake_run):
            rc = _run_pip_install(reqs)

        assert rc == 0
        argv = captured["argv"]
        assert argv[:4] == [sys.executable, "-m", "pip", "install"]
        assert "--upgrade" in argv
        idx = argv.index("--index-url")
        assert argv[idx + 1] == "https://microsoft.github.io/quicksand/simple/"
        eidx = argv.index("--extra-index-url")
        assert argv[eidx + 1] == "https://pypi.org/simple/"
        assert argv[-2:] == ["quicksand-qemu", "quicksand-cua"]

    def test_forwards_specifiers(self):
        captured: dict = {}

        def fake_run(argv, *_args, **_kwargs):
            captured["argv"] = argv
            return _DummyCompleted(0)

        reqs = [Requirement("quicksand-qemu==0.5.9"), Requirement("quicksand-ubuntu>=0.4")]
        with patch("quicksand.cli.install.subprocess.run", side_effect=fake_run):
            _run_pip_install(reqs)

        assert "quicksand-qemu==0.5.9" in captured["argv"]
        assert "quicksand-ubuntu>=0.4" in captured["argv"]


class TestInstall:
    def test_pip_failure_raises(self):
        with (
            patch(
                "quicksand.cli.install.subprocess.run",
                return_value=_DummyCompleted(1),
            ),
            pytest.raises(RuntimeError, match="pip install failed"),
        ):
            install("qemu")

    def test_no_requirements_raises(self):
        with pytest.raises(ValueError):
            install()

    def test_install_packages_returns_pip_exit_code(self):
        with patch(
            "quicksand.cli.install.subprocess.run",
            return_value=_DummyCompleted(2),
        ):
            assert _install_packages([Requirement("quicksand-ubuntu")]) == 2

    def test_install_packages_succeeds(self):
        with patch(
            "quicksand.cli.install.subprocess.run",
            return_value=_DummyCompleted(0),
        ):
            assert _install_packages([Requirement("quicksand-ubuntu")]) == 0
