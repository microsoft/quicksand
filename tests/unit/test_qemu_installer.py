"""Tests for quicksand_core.qemu.installer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from quicksand_core.host.arch import Architecture
from quicksand_core.qemu.installer import (
    ensure_runtime,
    install_qemu,
)


class TestInstallQemu:
    """Tests for install_qemu() platform dispatch."""

    @patch("quicksand_core.qemu.installer.shutil.which", return_value="/usr/bin/qemu-system-x86_64")
    @patch("quicksand_core.qemu.installer._detect_architecture", return_value=Architecture.X86_64)
    def test_skips_if_already_on_path(self, _mock_arch, _mock_which):
        """Should return early if QEMU is already on PATH."""
        install_qemu()  # Should not raise

    @patch("quicksand_core.qemu.installer._install_qemu_macos")
    @patch("quicksand_core.qemu.installer.shutil.which", return_value=None)
    @patch("quicksand_core.qemu.installer._platform.system", return_value="Darwin")
    @patch("quicksand_core.qemu.installer._detect_architecture", return_value=Architecture.ARM64)
    def test_dispatches_macos(self, _arch, _sys, _which, mock_install):
        install_qemu()
        mock_install.assert_called_once()

    @patch("quicksand_core.qemu.installer._install_qemu_linux")
    @patch("quicksand_core.qemu.installer.shutil.which", return_value=None)
    @patch("quicksand_core.qemu.installer._platform.system", return_value="Linux")
    @patch("quicksand_core.qemu.installer._detect_architecture", return_value=Architecture.X86_64)
    def test_dispatches_linux(self, _arch, _sys, _which, mock_install):
        install_qemu()
        mock_install.assert_called_once_with(Architecture.X86_64)

    @patch("quicksand_core.qemu.installer._install_qemu_windows")
    @patch("quicksand_core.qemu.installer.shutil.which", return_value=None)
    @patch("quicksand_core.qemu.installer._platform.system", return_value="Windows")
    @patch("quicksand_core.qemu.installer._detect_architecture", return_value=Architecture.X86_64)
    def test_dispatches_windows(self, _arch, _sys, _which, mock_install):
        install_qemu()
        mock_install.assert_called_once_with(Architecture.X86_64)

    @patch("quicksand_core.qemu.installer.shutil.which", return_value=None)
    @patch("quicksand_core.qemu.installer._platform.system", return_value="FreeBSD")
    @patch("quicksand_core.qemu.installer._detect_architecture", return_value=Architecture.X86_64)
    def test_raises_on_unsupported_platform(self, _arch, _sys, _which):
        with pytest.raises(RuntimeError, match="Unsupported platform"):
            install_qemu()


class TestEnsureRuntime:
    """Tests for ensure_runtime() resolve-or-install logic."""

    @patch("quicksand_core.qemu.platform.get_runtime")
    def test_returns_without_installing_when_found(self, mock_get_runtime):
        """Should return RuntimeInfo without calling install_qemu."""
        mock_runtime = MagicMock()
        mock_get_runtime.return_value = mock_runtime

        result = ensure_runtime()

        assert result is mock_runtime
        mock_get_runtime.assert_called_once()

    @patch("quicksand_core.qemu.platform.get_runtime")
    @patch("quicksand_core.qemu.installer.install_qemu")
    def test_installs_on_runtime_error(self, mock_install, mock_get_runtime):
        """Should call install_qemu and retry when get_runtime raises."""
        mock_runtime = MagicMock()
        mock_get_runtime.side_effect = [RuntimeError("not found"), mock_runtime]

        result = ensure_runtime()

        assert result is mock_runtime
        mock_install.assert_called_once()
        assert mock_get_runtime.call_count == 2

    @patch("quicksand_core.qemu.installer.install_qemu")
    def test_propagates_wrong_arch_error(self, mock_install):
        """Should re-raise _WrongArchError without attempting install."""
        from quicksand_core.qemu.platform import _WrongArchError

        with (
            patch(
                "quicksand_core.qemu.platform.get_runtime",
                side_effect=_WrongArchError("wrong arch"),
            ),
            pytest.raises(_WrongArchError),
        ):
            ensure_runtime()

        mock_install.assert_not_called()
