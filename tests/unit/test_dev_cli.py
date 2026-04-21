"""Unit tests for quicksand_dev CLI module."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from quicksand_core import BaseImageInfo
from quicksand_image_tools.cli import (
    cmd_build_base,
    cmd_build_image,
    cmd_init,
    discover_bases,
    main,
)

# ============================================================================
# Tests for helper functions
# ============================================================================


class TestDiscoverBases:
    """Tests for discover_bases function."""

    def test_discovers_bases_from_entry_points(self, tmp_dir):
        """Test discovering bases via entry points."""
        docker_dir = tmp_dir / "docker"
        docker_dir.mkdir()

        mock_provider = MagicMock()
        mock_provider.name = "test"
        mock_provider.type = "base"

        mock_module = MagicMock()
        mock_module._DOCKER_DIR = docker_dir
        mock_module.DISTRO_VERSION = "1.0.0"

        mock_ep = MagicMock()
        mock_ep.name = "test"
        mock_ep.value = "test_pkg:image"
        mock_ep.load.return_value = mock_provider

        with (
            patch("quicksand_image_tools.cli.entry_points", return_value=[mock_ep]),
            patch("importlib.import_module", return_value=mock_module),
        ):
            result = discover_bases()
            assert "test" in result
            assert result["test"].name == "test"
            assert result["test"].version == "1.0.0"

    def test_returns_empty_dict_when_no_bases(self):
        """Test returns empty dict when no entry points found."""
        with patch("quicksand_image_tools.cli.entry_points", return_value=[]):
            result = discover_bases()
            assert result == {}

    def test_handles_entry_point_load_error(self):
        """Test handles errors loading entry points gracefully."""
        mock_ep = MagicMock()
        mock_ep.name = "bad"
        mock_ep.value = "bad_pkg:image"
        mock_ep.load.side_effect = ImportError("Failed to load")

        with patch("quicksand_image_tools.cli.entry_points", return_value=[mock_ep]):
            result = discover_bases()
            assert result == {}

    def test_discovers_multiple_bases(self, tmp_dir):
        """Test discovering multiple bases."""
        ubuntu_dir = tmp_dir / "ubuntu"
        alpine_dir = tmp_dir / "alpine"
        ubuntu_dir.mkdir()
        alpine_dir.mkdir()

        ubuntu_provider = MagicMock(name="ubuntu", type="base")
        ubuntu_provider.name = "ubuntu"
        alpine_provider = MagicMock(name="alpine", type="base")
        alpine_provider.name = "alpine"

        mock_eps = [
            MagicMock(name="ubuntu", value="quicksand_ubuntu:image"),
            MagicMock(name="alpine", value="quicksand_alpine:image"),
        ]
        mock_eps[0].load.return_value = ubuntu_provider
        mock_eps[1].load.return_value = alpine_provider

        ubuntu_mod = MagicMock(_DOCKER_DIR=ubuntu_dir, DISTRO_VERSION="24.04.0")
        alpine_mod = MagicMock(_DOCKER_DIR=alpine_dir, DISTRO_VERSION="3.21.0")

        def _import(name):
            return {"quicksand_ubuntu": ubuntu_mod, "quicksand_alpine": alpine_mod}[name]

        with (
            patch("quicksand_image_tools.cli.entry_points", return_value=mock_eps),
            patch("importlib.import_module", side_effect=_import),
        ):
            result = discover_bases()
            assert len(result) == 2
            assert "ubuntu" in result
            assert "alpine" in result


# ============================================================================
# Tests for cmd_init
# ============================================================================


def _mock_bases(tmp_dir):
    """Create mock BaseImageInfo objects for testing."""
    ubuntu_dir = tmp_dir / "ubuntu_docker"
    alpine_dir = tmp_dir / "alpine_docker"
    ubuntu_dir.mkdir(exist_ok=True)
    alpine_dir.mkdir(exist_ok=True)

    return {
        "ubuntu": BaseImageInfo(name="ubuntu", docker_dir=ubuntu_dir, version="24.04.0"),
        "alpine": BaseImageInfo(name="alpine", docker_dir=alpine_dir, version="3.21.0"),
    }


class TestCmdInit:
    """Tests for cmd_init function."""

    def test_creates_directory_if_not_exists(self, tmp_dir):
        """Test that init creates the target directory."""
        target_dir = tmp_dir / "new_dir"
        args = argparse.Namespace(directory=target_dir, base="ubuntu")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=_mock_bases(tmp_dir)),
            patch("subprocess.run") as mock_run,
            patch("quicksand_image_tools.cli.cmd_build_base", return_value=0),
        ):
            mock_run.return_value = MagicMock(returncode=1)

            result = cmd_init(args)

            assert result == 0
            assert target_dir.exists()

    def test_creates_dockerfile_with_versioned_tag(self, tmp_dir):
        """Test that init creates Dockerfile with versioned tag."""
        args = argparse.Namespace(directory=tmp_dir, base="ubuntu")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=_mock_bases(tmp_dir)),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = cmd_init(args)

            assert result == 0
            dockerfile = tmp_dir / "Dockerfile"
            assert dockerfile.exists()
            content = dockerfile.read_text()
            assert "FROM quicksand/ubuntu-base:24.04.0" in content

    def test_builds_base_if_image_not_found(self, tmp_dir):
        """Test that init builds base image if not found."""
        args = argparse.Namespace(directory=tmp_dir, base="alpine")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=_mock_bases(tmp_dir)),
            patch("subprocess.run") as mock_run,
            patch("quicksand_image_tools.cli.cmd_build_base", return_value=0) as mock_build,
        ):
            mock_run.return_value = MagicMock(returncode=1)

            result = cmd_init(args)

            assert result == 0
            mock_build.assert_called_once()

    def test_error_when_no_dockerfile_and_no_base(self, tmp_dir, capsys):
        """Test error when no Dockerfile exists and no base specified."""
        args = argparse.Namespace(directory=tmp_dir, base=None)

        result = cmd_init(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "No Dockerfile found" in captured.err
        assert "ubuntu|alpine" in captured.err

    def test_skips_dockerfile_creation_if_exists(self, tmp_dir):
        """Test that init doesn't overwrite existing Dockerfile."""
        dockerfile = tmp_dir / "Dockerfile"
        dockerfile.write_text("FROM existing:image\n")

        args = argparse.Namespace(directory=tmp_dir, base="ubuntu")

        result = cmd_init(args)

        assert result == 0
        # Dockerfile should be unchanged
        assert dockerfile.read_text() == "FROM existing:image\n"

    def test_uses_latest_when_version_not_found(self, tmp_dir, capsys):
        """Test falls back to 'latest' when base not discovered."""
        args = argparse.Namespace(directory=tmp_dir, base="ubuntu")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value={}),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = cmd_init(args)

            assert result == 0
            dockerfile = tmp_dir / "Dockerfile"
            content = dockerfile.read_text()
            assert "FROM quicksand/ubuntu-base:latest" in content
            captured = capsys.readouterr()
            assert "using 'latest'" in captured.err

    def test_returns_error_when_build_base_fails(self, tmp_dir):
        """Test returns error when build-base fails."""
        args = argparse.Namespace(directory=tmp_dir, base="ubuntu")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=_mock_bases(tmp_dir)),
            patch("subprocess.run") as mock_run,
            patch("quicksand_image_tools.cli.cmd_build_base", return_value=1),
        ):
            mock_run.return_value = MagicMock(returncode=1)

            result = cmd_init(args)

            assert result == 1


# ============================================================================
# Tests for cmd_build_base
# ============================================================================


class TestCmdBuildBase:
    """Tests for cmd_build_base function.

    The Rust agent is compiled during Docker build via multi-stage build,
    so we no longer copy agent.py to the build context.
    """

    def test_builds_single_base_image(self, tmp_dir):
        """Test building a single base image."""
        docker_dir = tmp_dir / "ubuntu_docker"
        docker_dir.mkdir()
        (docker_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")

        bases = {"ubuntu": BaseImageInfo(name="ubuntu", docker_dir=docker_dir, version="24.04.0")}
        args = argparse.Namespace(base="ubuntu")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=bases),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = cmd_build_base(args)

            assert result == 0
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "docker" in cmd
            assert "build" in cmd
            assert "-t" in cmd
            assert "quicksand/ubuntu-base:24.04.0" in cmd
            assert "quicksand/ubuntu-base:latest" in cmd
            # Build should run in the docker_dir directly (no temp dir)
            assert call_args[1]["cwd"] == docker_dir

    def test_builds_all_base_images(self, tmp_dir):
        """Test building all base images."""
        ubuntu_dir = tmp_dir / "ubuntu_docker"
        alpine_dir = tmp_dir / "alpine_docker"
        ubuntu_dir.mkdir()
        alpine_dir.mkdir()
        (ubuntu_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")
        (alpine_dir / "Dockerfile").write_text("FROM alpine:3.21\n")

        bases = {
            "ubuntu": BaseImageInfo(name="ubuntu", docker_dir=ubuntu_dir, version="24.04.0"),
            "alpine": BaseImageInfo(name="alpine", docker_dir=alpine_dir, version="3.21.0"),
        }
        args = argparse.Namespace(base="all")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=bases),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            result = cmd_build_base(args)

            assert result == 0
            assert mock_run.call_count == 2

    def test_warns_when_package_not_found(self, capsys):
        """Test warns when base package not installed."""
        args = argparse.Namespace(base="ubuntu")

        with patch("quicksand_image_tools.cli.discover_bases", return_value={}):
            result = cmd_build_base(args)

            assert result == 0  # Still returns 0, just warns
            captured = capsys.readouterr()
            assert "ubuntu base not found" in captured.err
            assert "install quicksand[ubuntu]" in captured.err

    def test_returns_error_on_docker_build_failure(self, tmp_dir, capsys):
        """Test returns error when docker build fails."""
        docker_dir = tmp_dir / "docker"
        docker_dir.mkdir()
        (docker_dir / "Dockerfile").write_text("FROM ubuntu:24.04\n")

        bases = {"ubuntu": BaseImageInfo(name="ubuntu", docker_dir=docker_dir, version="24.04.0")}
        args = argparse.Namespace(base="ubuntu")

        with (
            patch("quicksand_image_tools.cli.discover_bases", return_value=bases),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="build failed")

            result = cmd_build_base(args)

            assert result == 1
            captured = capsys.readouterr()
            assert "Error building" in captured.err


# ============================================================================
# Tests for cmd_build_image
# ============================================================================


class TestCmdBuildImage:
    """Tests for cmd_build_image function.

    The Dockerfile should use multi-stage build to compile the Rust agent.
    We no longer copy agent.py to the build context.
    """

    def test_builds_image_successfully(self, tmp_dir):
        """Test successful image build."""
        dockerfile = tmp_dir / "Dockerfile"
        dockerfile.write_text("FROM quicksand/ubuntu-base:24.04.0\n")

        args = argparse.Namespace(dockerfile=dockerfile, output=None, cache_dir=tmp_dir / "cache")

        with patch("quicksand_image_tools.cli.build_image") as mock_build:
            mock_build.return_value = tmp_dir / "output.qcow2"

            result = cmd_build_image(args)

            assert result == 0
            mock_build.assert_called_once_with(
                dockerfile,
                output_path=None,
                cache_dir=tmp_dir / "cache",
            )

    def test_error_when_dockerfile_not_found(self, tmp_dir, capsys):
        """Test error when Dockerfile doesn't exist."""
        dockerfile = tmp_dir / "nonexistent" / "Dockerfile"
        args = argparse.Namespace(dockerfile=dockerfile, output=None, cache_dir=None)

        result = cmd_build_image(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "Dockerfile not found" in captured.err

    def test_returns_error_on_build_failure(self, tmp_dir, capsys):
        """Test returns error when build fails."""
        dockerfile = tmp_dir / "Dockerfile"
        dockerfile.write_text("FROM quicksand/ubuntu-base:24.04.0\n")

        args = argparse.Namespace(dockerfile=dockerfile, output=None, cache_dir=tmp_dir / "cache")

        with patch("quicksand_image_tools.cli.build_image", side_effect=Exception("Build failed")):
            result = cmd_build_image(args)

            assert result == 1
            captured = capsys.readouterr()
            assert "Build failed" in captured.err

    def test_passes_output_path(self, tmp_dir):
        """Test that output path is passed to build_image."""
        dockerfile = tmp_dir / "Dockerfile"
        dockerfile.write_text("FROM quicksand/ubuntu-base:24.04.0\n")
        output = tmp_dir / "custom.qcow2"

        args = argparse.Namespace(dockerfile=dockerfile, output=output, cache_dir=None)

        with patch("quicksand_image_tools.cli.build_image") as mock_build:
            mock_build.return_value = output

            cmd_build_image(args)

            mock_build.assert_called_once_with(
                dockerfile,
                output_path=output,
                cache_dir=None,
            )


# ============================================================================
# Tests for main() argument parsing
# ============================================================================


class TestMainArgumentParsing:
    """Tests for main() CLI argument parsing."""

    def test_init_with_directory_and_base(self):
        """Test parsing init command with directory and base."""
        with (
            patch("quicksand_image_tools.cli.cmd_init", return_value=0) as mock_init,
            patch("sys.argv", ["quicksand-image-tools", "init", "./my-image", "ubuntu"]),
        ):
            result = main()

            assert result == 0
            args = mock_init.call_args[0][0]
            assert args.directory == Path("./my-image")
            assert args.base == "ubuntu"

    def test_init_default_directory(self):
        """Test init command defaults to current directory."""
        with (
            patch("quicksand_image_tools.cli.cmd_init", return_value=0) as mock_init,
            patch("sys.argv", ["quicksand-image-tools", "init"]),
        ):
            main()

            args = mock_init.call_args[0][0]
            assert args.directory == Path(".")
            assert args.base is None

    def test_build_base_single(self):
        """Test parsing build-base with single base."""
        with (
            patch("quicksand_image_tools.cli.cmd_build_base", return_value=0) as mock_build,
            patch("sys.argv", ["quicksand-image-tools", "build-base", "alpine"]),
        ):
            main()

            args = mock_build.call_args[0][0]
            assert args.base == "alpine"

    def test_build_base_defaults_to_all(self):
        """Test build-base defaults to 'all'."""
        with (
            patch("quicksand_image_tools.cli.cmd_build_base", return_value=0) as mock_build,
            patch("sys.argv", ["quicksand-image-tools", "build-base"]),
        ):
            main()

            args = mock_build.call_args[0][0]
            assert args.base == "all"

    def test_build_image_with_output(self):
        """Test parsing build-image with output option."""
        with (
            patch("quicksand_image_tools.cli.cmd_build_image", return_value=0) as mock_build,
            patch(
                "sys.argv",
                ["quicksand-image-tools", "build-image", "Dockerfile", "-o", "out.qcow2"],
            ),
        ):
            main()

            args = mock_build.call_args[0][0]
            assert args.dockerfile == Path("Dockerfile")
            assert args.output == Path("out.qcow2")

    def test_verbose_flag(self):
        """Test verbose flag is parsed."""
        with (
            patch("quicksand_image_tools.cli.cmd_init", return_value=0),
            patch("quicksand_image_tools.cli._setup_logging") as mock_logging,
            patch("sys.argv", ["quicksand-image-tools", "-v", "init", ".", "ubuntu"]),
        ):
            main()

            # Verbose should be True
            mock_logging.assert_called_with(True)

    def test_package_init_parsed(self):
        """Test parsing package init command."""
        with (
            patch("quicksand_image_tools.cli.cmd_package_init", return_value=0) as mock_pkg_init,
            patch("sys.argv", ["quicksand-image-tools", "package", "init", "mylinux", "ubuntu"]),
        ):
            result = main()

            assert result == 0
            args = mock_pkg_init.call_args[0][0]
            assert args.name == "mylinux"
            assert args.base == "ubuntu"
            assert args.output_dir is None

    def test_package_init_with_output_dir(self):
        """Test parsing package init with --output-dir."""
        with (
            patch("quicksand_image_tools.cli.cmd_package_init", return_value=0) as mock_pkg_init,
            patch(
                "sys.argv",
                [
                    "quicksand-image-tools",
                    "package",
                    "init",
                    "mylinux",
                    "ubuntu",
                    "--output-dir",
                    "/tmp/pkg",
                ],
            ),
        ):
            main()

            args = mock_pkg_init.call_args[0][0]
            assert args.output_dir == Path("/tmp/pkg")


# ============================================================================
