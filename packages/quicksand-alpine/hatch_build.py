"""Hatch build hook for platform-specific Alpine VM image wheels."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

logger = logging.getLogger(__name__)


def _read_distro_version(root: Path) -> str:
    """Read DISTRO_VERSION from __init__.py without importing the module."""
    init_file = root / "quicksand_alpine" / "__init__.py"
    content = init_file.read_text()
    match = re.search(r'^DISTRO_VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find DISTRO_VERSION in {init_file}")
    return match.group(1)


class AlpineImageBuildHook(BuildHookInterface):
    """Build hook that packages Alpine VM images with platform-specific wheel tags."""

    PLUGIN_NAME = "alpine-image"

    def initialize(self, version: str, build_data: dict) -> None:
        """Verify the image exists (building if needed) and set platform-specific wheel tags."""
        from quicksand_image_tools.build_utils import get_image_arch, set_platform_wheel_tag

        if not set_platform_wheel_tag(build_data, target_name=self.target_name, version=version):
            return
        arch = get_image_arch()

        # Check if image exists
        distro_version = _read_distro_version(Path(self.root))

        pkg_dir = Path(self.root) / "quicksand_alpine"
        images_dir = pkg_dir / "images"
        image_path = images_dir / f"alpine-{distro_version}-{arch}.qcow2"
        dockerfile_path = Path(self.root) / "quicksand_alpine" / "docker" / "Dockerfile"

        if not image_path.exists():
            self.app.display_info(f"Image not found: {image_path.name}, building...")
            self._build_image(dockerfile_path, image_path)

        self.app.display_info(f"Including image: {image_path}")

    def _build_image(self, dockerfile: Path, output: Path) -> None:
        """Build the VM image using quicksand-image-tools.

        The Dockerfile uses a multi-stage build to compile the Rust agent.
        The agent source is accessed via symlink at docker/agent/.
        """
        try:
            from quicksand_image_tools import build_image

            logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
            build_log = logging.getLogger("quicksand-alpine")

            output.parent.mkdir(parents=True, exist_ok=True)
            build_image(dockerfile, output_path=output, log=build_log)

        except ImportError as e:
            raise RuntimeError(
                f"quicksand-image-tools is required to build images but failed to import: {e}\n\n"
                f"This should be installed automatically as a build dependency.\n"
                f"If building manually, install it first:\n"
                f"  pip install quicksand-image-tools"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Failed to build image: {e}\n\nMake sure Docker is running and accessible."
            ) from e
