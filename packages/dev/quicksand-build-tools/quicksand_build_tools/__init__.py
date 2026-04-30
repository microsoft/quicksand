"""Shared build utilities for bundling native binaries into quicksand platform wheels.

Provides platform-specific logic for:
- Discovering and copying shared library dependencies
- Rewriting library paths (install_name_tool on macOS, patchelf on Linux)
- System library blocklists (libs that must NOT be bundled)
- Binary verification (catch missing deps at build time)
- Platform wheel tagging
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sysconfig
import tempfile
from pathlib import Path
from typing import Protocol

# System libraries that must NOT be bundled (loaded from host OS).
# Checked by prefix matching against the library filename.
SYSTEM_LIBS: dict[str, set[str]] = {
    "darwin": set(),  # macOS system libs don't exist on disk (dyld shared cache)
    "linux": {
        "libc.",
        "libm.",
        "libpthread.",
        "libdl.",
        "librt.",
        "ld-linux",
        "libgcc_s.",
        "libstdc++.",
        "linux-vdso",
    },
    "windows": {
        "kernel32",
        "ntdll",
        "msvcrt",
        "user32",
        "gdi32",
        "advapi32",
        "shell32",
        "ole32",
        "oleaut32",
        "ws2_32",
        "crypt32",
        "secur32",
        "api-ms-",
        "ext-ms-",
        "vcruntime",
        "ucrtbase",
        "msvcp",
    },
}


class DisplayApp(Protocol):
    """Protocol for Hatchling's app display interface."""

    def display_info(self, message: str) -> None: ...
    def display_warning(self, message: str) -> None: ...


class BinaryBundler:
    """Bundles native binaries and their shared library dependencies into a wheel.

    Usage::

        bundler = BinaryBundler(app)  # app = Hatchling BuildHookInterface
        bundler.bundle(binary_path, bin_dir)
        bundler.verify(binary_path, bin_dir)
    """

    def __init__(self, app: DisplayApp) -> None:
        self.app = app

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def should_bundle(self, lib_path: str, plat: str) -> bool:
        """Check if a library should be bundled.

        Uses a consistent pattern across all platforms:
        1. Skip @-prefixed paths (loader-relative, macOS only)
        2. Skip if file doesn't exist on disk (system lib in shared cache)
        3. Skip if in system blocklist
        4. Otherwise, bundle it
        """
        if lib_path.startswith("@"):
            return False
        if not Path(lib_path).exists():
            return False
        lib_name = Path(lib_path).name.lower()
        blocklist = SYSTEM_LIBS.get(plat, set())
        return not any(lib_name.startswith(prefix) for prefix in blocklist)

    def bundle(self, binary: Path, bin_dir: Path) -> None:
        """Bundle a binary's shared library dependencies into bin_dir/lib/.

        Dispatches to the appropriate platform-specific bundler.
        """
        system = platform.system().lower()
        if system == "darwin":
            self.bundle_macos_dylibs(binary, bin_dir)
        elif system == "linux":
            self.bundle_linux_libs(binary, bin_dir)
        elif system == "windows":
            self.bundle_windows_dlls(binary, bin_dir)

    def verify(self, binary: Path, bin_dir: Path) -> None:
        """Verify a bundled binary runs without missing library errors.

        Clears library path env vars to force using only bundled libs,
        then runs the binary with --version.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "LD_LIBRARY"))}

        lib_dir = bin_dir / "lib"
        if lib_dir.exists():
            if platform.system() == "Windows":
                env["PATH"] = str(lib_dir) + os.pathsep + env.get("PATH", "")
            else:
                env["LD_LIBRARY_PATH"] = str(lib_dir)

        try:
            result = subprocess.run(
                [str(binary), "--version"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Bundled binary {binary.name} timed out during verification.\n"
                "The binary may have missing dependencies or other issues."
            ) from e
        if result.returncode != 0:
            raise RuntimeError(
                f"Bundled binary {binary.name} failed verification:\n"
                f"  Exit code: {result.returncode}\n"
                f"  Stderr: {result.stderr}\n"
                "A required library dependency was not bundled correctly."
            )
        self.app.display_info(f"Verified: {binary.name}")

    def set_platform_wheel_tag(self, build_data: dict) -> None:
        """Set build_data fields for a platform-specific py3-none wheel.

        On Windows ARM64, overrides the tag from ``win_amd64`` to ``win_arm64``
        when native hardware is ARM64 (Python may report amd64 under emulation).
        """
        build_data["pure_python"] = False
        platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")

        # Detect native arch on Windows ARM64
        import platform as _platform

        if _platform.system() == "Windows":
            try:
                import winreg  # ty:ignore[unresolved-attribute]

                key = winreg.OpenKey(  # ty:ignore[unresolved-attribute]
                    winreg.HKEY_LOCAL_MACHINE,  # ty:ignore[unresolved-attribute]
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                )
                native, _ = winreg.QueryValueEx(key, "PROCESSOR_ARCHITECTURE")  # ty:ignore[unresolved-attribute]
                winreg.CloseKey(key)  # ty:ignore[unresolved-attribute]
                if native.lower() in ("arm64", "aarch64") and "win_amd64" in platform_tag:
                    platform_tag = "win_arm64"
            except Exception:
                pass

        # PyPI requires manylinux tags for Linux wheels (PEP 600)
        if platform_tag.startswith("linux_"):
            platform_tag = platform_tag.replace("linux_", "manylinux_2_17_", 1)

        build_data["tag"] = f"py3-none-{platform_tag}"

    def force_include_bin_dir(self, bin_dir: Path, root: Path, build_data: dict) -> None:
        """Add all files in bin_dir to the wheel's force_include."""
        force_include = build_data.setdefault("force_include", {})
        for f in bin_dir.rglob("*"):
            if f.is_file():
                rel_path = f.relative_to(root)
                force_include[str(f)] = str(rel_path)

    def make_executable(self, bin_dir: Path) -> None:
        """chmod +x all files in bin_dir."""
        for f in bin_dir.iterdir():
            if f.is_file():
                f.chmod(f.stat().st_mode | 0o755)

    # -----------------------------------------------------------------
    # macOS
    # -----------------------------------------------------------------

    def bundle_macos_dylibs(
        self,
        binary: Path,
        bin_dir: Path,
        *,
        entitlements_plist: str | None = None,
    ) -> None:
        """Copy dylib dependencies and rewrite paths for macOS.

        Args:
            binary: The binary to bundle dependencies for.
            bin_dir: Destination directory (libs go into bin_dir/lib/).
            entitlements_plist: Optional entitlements plist XML string for codesigning
                the main binary. If None, signs with ad-hoc only.
        """
        lib_dir = bin_dir / "lib"
        lib_dir.mkdir(exist_ok=True)

        self._process_binary_dylibs(binary, bin_dir, lib_dir, set())

        # Re-sign all modified binaries (install_name_tool invalidates signatures)
        self.app.display_info(f"Re-signing {binary.name}...")

        # Sign dylibs with ad-hoc signature
        for f in lib_dir.glob("*.dylib"):
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(f)],
                capture_output=True,
            )

        # Sign the main binary
        if entitlements_plist:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".plist", delete=False) as f:
                f.write(entitlements_plist)
                entitlements_path = f.name
            try:
                subprocess.run(
                    [
                        "codesign",
                        "--force",
                        "--sign",
                        "-",
                        "--entitlements",
                        entitlements_path,
                        str(binary),
                    ],
                    capture_output=True,
                )
            finally:
                Path(entitlements_path).unlink(missing_ok=True)
        else:
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(binary)],
                capture_output=True,
            )

        self.app.display_info(f"Re-signed {len(list(lib_dir.glob('*.dylib'))) + 1} files")

    def _process_binary_dylibs(
        self, binary: Path, bin_dir: Path, lib_dir: Path, processed: set[str]
    ) -> None:
        """Recursively process dylib dependencies for a binary."""
        if str(binary) in processed:
            return
        processed.add(str(binary))

        result = subprocess.run(
            ["otool", "-L", str(binary)],
            capture_output=True,
            text=True,
        )

        dylibs_to_rewrite: list[tuple[str, str]] = []

        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            dylib_path = line.split()[0]

            if not self.should_bundle(dylib_path, "darwin"):
                continue

            dylib_name = Path(dylib_path).name
            dest = lib_dir / dylib_name

            if not dest.exists() and Path(dylib_path).exists():
                shutil.copy(dylib_path, dest)
                dest.chmod(dest.stat().st_mode | 0o755)
                self._process_binary_dylibs(dest, bin_dir, lib_dir, processed)

            dylibs_to_rewrite.append((dylib_path, dylib_name))

        is_in_lib = binary.parent == lib_dir
        loader_prefix = "@loader_path" if is_in_lib else "@loader_path/lib"

        for dylib_path, dylib_name in dylibs_to_rewrite:
            subprocess.run(
                [
                    "install_name_tool",
                    "-change",
                    dylib_path,
                    f"{loader_prefix}/{dylib_name}",
                    str(binary),
                ],
                capture_output=True,
            )

        if binary.suffix == ".dylib" or ".dylib" in binary.name:
            subprocess.run(
                [
                    "install_name_tool",
                    "-id",
                    f"@loader_path/{binary.name}",
                    str(binary),
                ],
                capture_output=True,
            )

    # -----------------------------------------------------------------
    # Linux
    # -----------------------------------------------------------------

    def bundle_linux_libs(self, binary: Path, bin_dir: Path) -> None:
        """Copy shared library dependencies and set RPATH for Linux."""
        if not shutil.which("patchelf"):
            self.app.display_warning("patchelf not found, skipping library bundling")
            return

        lib_dir = bin_dir / "lib"
        lib_dir.mkdir(exist_ok=True)

        self._process_linux_libs(binary, lib_dir, set())

        subprocess.run(
            ["patchelf", "--set-rpath", "$ORIGIN/lib", str(binary)],
            check=True,
        )

    def _process_linux_libs(self, binary: Path, lib_dir: Path, processed: set[str]) -> None:
        """Recursively process shared library dependencies for a Linux binary."""
        if str(binary) in processed:
            return
        processed.add(str(binary))

        result = subprocess.run(
            ["ldd", str(binary)],
            capture_output=True,
            text=True,
        )

        for line in result.stdout.splitlines():
            if "=>" not in line or "not found" in line:
                continue

            parts = line.split("=>")
            if len(parts) != 2:
                continue

            lib_path = parts[1].strip().split()[0]
            if not lib_path.startswith("/"):
                continue

            if not self.should_bundle(lib_path, "linux"):
                continue

            lib_name = Path(lib_path).name
            dest = lib_dir / lib_name
            if not dest.exists() and Path(lib_path).exists():
                shutil.copy(lib_path, dest)
                dest.chmod(dest.stat().st_mode | 0o755)
                self._process_linux_libs(dest, lib_dir, processed)
                subprocess.run(
                    ["patchelf", "--set-rpath", "$ORIGIN", str(dest)],
                    capture_output=True,
                )

    # -----------------------------------------------------------------
    # Windows
    # -----------------------------------------------------------------

    def bundle_windows_dlls(self, binary: Path, bin_dir: Path) -> None:
        """Copy DLL dependencies for Windows.

        Uses a simple approach: copy all DLLs from the binary's install directory.
        """
        install_dir = binary.parent
        search_dirs = [install_dir, install_dir / "dll"]

        count = 0
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue

            for dll in search_dir.glob("*.dll"):
                if not self.should_bundle(str(dll), "windows"):
                    continue

                dest = bin_dir / dll.name
                if not dest.exists():
                    shutil.copy(dll, dest)
                    count += 1

        if count > 0:
            self.app.display_info(f"Bundled {count} Windows DLLs from {install_dir}")


__all__ = ["SYSTEM_LIBS", "BinaryBundler", "DisplayApp"]
