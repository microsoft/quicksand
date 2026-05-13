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

# Retained for any external code still importing it; the loader no longer
# branches on a numeric version. Save dispatch lives in the writer + the
# basename resolution in _resolve_chain_entry.
SAVE_VERSION = 7


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
        if result is None and not image.startswith("quicksand-"):
            result = self._resolve_base_by_name(f"quicksand-{image}", arch=arch)
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
        """Validate save structure: every chain entry resolves to a file.

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

        if not manifest.chain:
            raise ValueError(f"Save manifest has empty chain: {path}")

        missing: list[str] = []
        for entry in manifest.chain:
            if _resolve_chain_entry(entry, path) is None:
                missing.append(entry)
        if missing:
            raise ValueError(
                f"Save at {path} references missing overlays: "
                + ", ".join(missing)
                + "\nThe overlay cache may have been GC'd, or the save dir is incomplete."
            )

        return manifest

    # =================================================================
    # Internal: save resolution
    # =================================================================

    def _resolve_save(self, save_dir: Path) -> ResolvedImage:
        """Load a save directory and resolve its base image.

        Each chain entry is resolved by searching, in order, the save
        dir's own ``overlays/`` subdir then the per-user overlay cache.
        The first hit wins, so a bundled save stays self-contained even
        if the overlay cache happens to hold a same-named file.
        """
        manifest = _load_manifest(save_dir)

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

        parent_name = manifest.config.image
        parent_resolved = self._resolve_base_by_name(parent_name, arch=image_arch)
        if parent_resolved is None:
            arch_msg = f" for {image_arch}" if image_arch else ""
            raise RuntimeError(
                f"Base image '{parent_name}'{arch_msg} not installed.\n"
                f"Install it with: quicksand install {parent_name}"
                + (f" --arch {image_arch}" if image_arch else "")
            )

        overlays: list[Path] = []
        missing: list[str] = []
        for entry in manifest.chain:
            resolved = _resolve_chain_entry(entry, save_dir)
            if resolved is None:
                missing.append(entry)
            else:
                overlays.append(resolved)
        if missing:
            raise RuntimeError(
                f"Save at {save_dir} references missing overlays:\n  "
                + "\n  ".join(missing)
                + "\nLook in <save>/overlays/ or the per-user overlay cache. "
                "The overlay cache may have been GC'd, or the save dir is incomplete."
            )

        logger.info("Loaded save: parent=%s, chain_len=%d", parent_name, len(overlays))

        full_chain = list(parent_resolved.chain) + overlays

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


def _resolve_chain_entry(entry: str, save_dir: Path) -> Path | None:
    """Locate an overlay file by basename: save dir's overlays/ then the cache.

    Returns the resolved path or ``None`` if neither location holds it.
    Search order is "local first" so a bundled save stays self-contained
    even when the overlay cache happens to hold a file with the same basename.
    """
    local = save_dir / FilePatterns.OVERLAYS_DIR / entry
    if local.exists():
        return local
    try:
        from .._overlay_cache import get_overlays_dir

        pool_path = get_overlays_dir() / entry
        if pool_path.exists():
            return pool_path
    except Exception:
        pass
    return None


def _load_manifest(save_dir: Path) -> SaveManifest:
    """Load and parse manifest.json from a save directory.

    Handles in-place migration of legacy manifest shapes so the rest of
    the code only deals with the current schema:

    * Pre-Phase-6 ("v6") manifests have no ``chain`` field; we synthesize
      it from ``<save>/overlays/*.qcow2``.
    * Early Phase-6 ("v7") manifests have absolute cache paths in
      ``chain``; we reduce each to its basename.
    * Old ``version`` / ``format`` discriminator fields are dropped — the
      new resolver doesn't need them.
    """
    import json

    manifest_path = save_dir / FilePatterns.MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"Missing {FilePatterns.MANIFEST} in save: {save_dir}")
    data = json.loads(manifest_path.read_text())

    chain = data.get("chain")
    if not chain:
        overlays_dir = save_dir / FilePatterns.OVERLAYS_DIR
        if overlays_dir.is_dir():
            chain = sorted(p.name for p in overlays_dir.glob("*.qcow2"))
        else:
            chain = []
    else:
        chain = [Path(entry).name for entry in chain]
    data["chain"] = chain

    # Drop legacy discriminator fields — no longer load-bearing.
    data.pop("version", None)
    data.pop("format", None)

    return SaveManifest.model_validate(data)
