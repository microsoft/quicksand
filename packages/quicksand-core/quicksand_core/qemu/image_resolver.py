"""Image resolution — unified lookup for base images and saves.

Resolution order for string image references:

1. Explicit path (file or directory on disk) — load as save
2. ``$CWD/.quicksand/sandboxes/{name}/`` — local save
3. ``~/.quicksand/sandboxes/{name}/`` — global save
4. Entry points (``quicksand.images`` group) — base image packages
5. Error
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from pathlib import Path

from .._types import FilePatterns, ResolvedImage, SaveManifest
from ..host.arch import Architecture, _detect_architecture

logger = logging.getLogger("quicksand.image")

# Current save file format version
SAVE_VERSION = 6


class ImageResolver:
    """Resolves an image name to concrete artifacts.

    Saves are always directories. Tar files are not supported — users must
    extract them before use.
    """

    # =================================================================
    # Public: image resolution
    # =================================================================

    def resolve(self, image: str, *, arch: str | None = None) -> ResolvedImage:
        """Resolve an image reference to concrete paths.

        Args:
            image: Image name (``"ubuntu"``), save name, or filesystem path.
            arch: Force a specific image architecture (e.g. ``"amd64"``).
                  When set, the sandbox will use TCG emulation if the host
                  architecture differs.
        """
        # 1-3. Check filesystem: explicit path, local save, global save
        save_dir = self._find_save_dir(image)
        if save_dir is not None:
            return self._resolve_save(save_dir)

        # 4. Entry points (quicksand.images)
        result = self._resolve_base_by_name(image, arch=arch)
        if result is not None:
            # Tag with guest_arch when cross-building
            if arch is not None:
                host_arch = _detect_architecture()
                image_arch = Architecture.from_str(arch).image_arch
                if image_arch != str(host_arch):
                    result = ResolvedImage(
                        name=result.name,
                        chain=result.chain,
                        kernel=result.kernel,
                        initrd=result.initrd,
                        guest_arch=image_arch,
                    )
            return result

        # 5. Fail
        raise RuntimeError(
            f"Image not found: '{image}'\n"
            f"Not a local/global save and no matching entry point.\n"
            f"Install it with: quicksand install {image}"
        )

    # =================================================================
    # Public: save validation
    # =================================================================

    def validate_save(self, path: Path) -> SaveManifest:
        """Validate save structure: manifest version and overlays directory.

        Args:
            path: Path to the save directory.

        Returns:
            The parsed SaveManifest.

        Raises:
            ValueError: If save is invalid.
        """
        if not path.is_dir():
            raise ValueError(f"Save path is not a directory: {path}")

        manifest = _load_manifest(path)

        if manifest.version > SAVE_VERSION:
            raise ValueError(
                f"Save version {manifest.version} is newer than supported version {SAVE_VERSION}"
            )

        overlays_dir = path / FilePatterns.OVERLAYS_DIR
        if not overlays_dir.is_dir():
            raise ValueError(f"Missing {FilePatterns.OVERLAYS_DIR}/ directory in save: {path}")

        overlays = sorted(overlays_dir.glob("*.qcow2"))
        if not overlays:
            raise ValueError(f"No overlay files found in {overlays_dir}")

        return manifest

    # =================================================================
    # Internal: save resolution
    # =================================================================

    def _resolve_save(self, save_dir: Path) -> ResolvedImage:
        """Load a save directory and resolve its base image."""
        manifest = _load_manifest(save_dir)

        if manifest.version > SAVE_VERSION:
            raise ValueError(
                f"Save version {manifest.version} is newer than supported version {SAVE_VERSION}"
            )

        # Read overlays from the directory (canonical source)
        overlays_dir = save_dir / FilePatterns.OVERLAYS_DIR
        if not overlays_dir.is_dir():
            raise ValueError(f"Missing {FilePatterns.OVERLAYS_DIR}/ directory in save: {save_dir}")
        overlays = sorted(overlays_dir.glob("*.qcow2"))
        if not overlays:
            raise ValueError(f"No overlay files found in {overlays_dir}")

        # Cross-arch detection
        host_arch = _detect_architecture()
        save_arch = manifest.arch
        image_arch: str | None = None

        if save_arch is not None and save_arch != str(host_arch):
            image_arch = Architecture.from_str(save_arch).image_arch
            logger.warning(
                "Save was created on %s but host is %s. "
                "Running in TCG (software emulation) — performance will be degraded.",
                save_arch,
                host_arch,
            )

        # Recursively resolve the parent image to get its full chain
        parent_name = manifest.config.image
        parent_resolved = self._resolve_base_by_name(parent_name, arch=image_arch)

        if parent_resolved is None:
            arch_msg = f" for {image_arch}" if image_arch else ""
            raise RuntimeError(
                f"Base image '{parent_name}'{arch_msg} not installed.\n"
                f"Install it with: quicksand install {parent_name}"
                + (f" --arch {image_arch}" if image_arch else "")
            )

        # Merge: parent's full chain + this save's overlays
        full_chain = list(parent_resolved.chain) + overlays

        logger.info(
            "Loaded save: parent=%s, own_overlays=%d, total_chain=%d",
            parent_name,
            len(overlays),
            len(full_chain),
        )

        return ResolvedImage(
            name=parent_name,
            chain=full_chain,
            kernel=parent_resolved.kernel,
            initrd=parent_resolved.initrd,
            guest_arch=image_arch,
        )

    # =================================================================
    # Internal: base image resolution
    # =================================================================

    @staticmethod
    def _resolve_base_by_name(name: str, *, arch: str | None = None) -> ResolvedImage | None:
        """Look up a base image by name via quicksand.images entry points."""
        for ep in entry_points(group="quicksand.images"):
            if ep.name != name:
                continue
            try:
                provider = ep.load()
                result = provider.resolve(arch=arch)
                if result.name != name:
                    # Provider may return a generic name; override with the
                    # entry-point name for consistency.
                    result = ResolvedImage(
                        name=name,
                        chain=result.chain,
                        kernel=result.kernel,
                        initrd=result.initrd,
                    )
                logger.info("Resolved base image via entry point: %s", name)
                return result
            except Exception:
                logger.debug("Failed to load entry point %s", ep.name, exc_info=True)
                continue
        return None

    # =================================================================
    # Internal: utilities
    # =================================================================

    @staticmethod
    def _find_save_dir(image: str) -> Path | None:
        """Check filesystem locations for a save matching the image name.

        Checks in order: explicit path, local saves, global saves.
        Tar files are not supported — raises an error if encountered.
        """
        candidates = [
            Path(image),  # 1. Explicit path
            Path.cwd() / ".quicksand" / "sandboxes" / image,  # 2. Local save
            Path.home() / ".quicksand" / "sandboxes" / image,  # 3. Global save
        ]
        for candidate in candidates:
            if candidate.is_file():
                if candidate.name.endswith((".tar.gz", ".tar")):
                    raise RuntimeError(
                        f"Tar save files are no longer supported: {candidate}\n"
                        f"Extract the archive first: tar xzf {candidate}"
                    )
                # Not a tar — skip (not a save directory)
                continue
            if candidate.is_dir() and (candidate / FilePatterns.MANIFEST).exists():
                logger.info("Found save directory: %s", candidate)
                return candidate
        return None


def _load_manifest(save_dir: Path) -> SaveManifest:
    """Load and parse manifest.json from a save directory."""
    manifest_path = save_dir / FilePatterns.MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"Missing {FilePatterns.MANIFEST} in save: {save_dir}")
    return SaveManifest.model_validate_json(manifest_path.read_text())
