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

        Additionally runs a platform-specific isolation check that
        catches the failure mode where the bundler missed a lib but the
        ``--version`` run still succeeds because the build machine has
        the lib installed system-wide:

        - **Linux**: re-runs ``ldd`` and asserts every NEEDED entry
          resolves under ``bin_dir`` or matches the SYSTEM_LIBS
          blocklist.
        - **macOS**: parses ``otool -L`` and asserts every load command
          is ``@loader_path/...``, ``/usr/lib/...``, or
          ``/System/Library/...``.  Anything from ``/opt/homebrew/`` or
          similar means the bundler missed a dylib.
        - **Windows**: re-runs ``--version`` with PATH scrubbed to just
          ``%SystemRoot%\\System32`` so the loader can only resolve DLLs
          via the binary's app-dir (``bin_dir``) or System32.
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

        system = platform.system().lower()
        if system == "linux":
            self._verify_linux_isolation(binary, bin_dir)
        elif system == "darwin":
            self._verify_macos_isolation(binary, bin_dir)
        elif system == "windows":
            self._verify_windows_isolation(binary, bin_dir)

        self.app.display_info(f"Verified: {binary.name}")

    def _verify_linux_isolation(self, binary: Path, bin_dir: Path) -> None:
        """Assert ldd resolves every NEEDED lib to a bundled or blocklisted path.

        Runs ``ldd`` against the bundled binary with ``LD_LIBRARY_PATH``
        scrubbed (the binary's RPATH is what should be doing the work).
        Any lib that resolves into a system path like ``/usr/lib/...`` —
        and is not in the SYSTEM_LIBS blocklist — means the bundler
        missed it and the wheel will break on hosts without that lib.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "LD_LIBRARY"))}
        result = subprocess.run(
            ["ldd", str(binary)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ldd failed against bundled {binary.name}:\n{result.stderr}")

        bin_dir_resolved = bin_dir.resolve()
        blocklist = SYSTEM_LIBS["linux"]
        leaked: list[str] = []
        not_found: list[str] = []

        for line in result.stdout.splitlines():
            line = line.strip()
            if "=>" not in line:
                continue
            if "not found" in line:
                not_found.append(line)
                continue
            parts = line.split("=>")
            if len(parts) != 2:
                continue
            lib_path = parts[1].strip().split()[0]
            if not lib_path.startswith("/"):
                continue

            lib_name = Path(lib_path).name.lower()
            if any(lib_name.startswith(prefix) for prefix in blocklist):
                continue

            try:
                Path(lib_path).resolve().relative_to(bin_dir_resolved)
            except ValueError:
                leaked.append(f"{Path(lib_path).name} <- {lib_path}")

        if not_found or leaked:
            msgs: list[str] = []
            if not_found:
                msgs.append("Unresolved NEEDED libs:\n  " + "\n  ".join(not_found))
            if leaked:
                msgs.append(
                    "Bundled binary resolved against system libs (not bundled):\n  "
                    + "\n  ".join(leaked)
                )
            raise RuntimeError(
                f"Verification of bundled {binary.name} failed:\n"
                + "\n".join(msgs)
                + "\nThe wheel would break on hosts missing these libs."
            )

    def _verify_macos_isolation(self, binary: Path, bin_dir: Path) -> None:
        """Assert otool -L resolves every load command to @loader_path or system.

        Apple frameworks ship in the dyld shared cache and must be loaded
        via ``/usr/lib/...`` or ``/System/Library/...``.  Anything else
        absolute (e.g. ``/opt/homebrew/Cellar/...``) means
        ``install_name_tool`` did not rewrite the load command — i.e.
        ``bundle_macos_dylibs`` skipped that dylib — and the wheel will
        fail to load on machines without that exact path.
        """
        result = subprocess.run(
            ["otool", "-L", str(binary)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"otool -L failed against bundled {binary.name}:\n{result.stderr}")

        leaked: list[str] = []
        # otool -L emits "<path>:" as the first line, then one tab-indented
        # load command per line.
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            load_path = line.split()[0]

            if load_path.startswith("@"):
                # @loader_path / @rpath / @executable_path — bundled-relative
                continue
            if load_path.startswith(("/usr/lib/", "/System/Library/")):
                # Apple system frameworks — always present, must not bundle
                continue

            leaked.append(load_path)

        if leaked:
            raise RuntimeError(
                f"Verification of bundled {binary.name} failed:\n"
                "Load commands point outside the bundle / Apple system paths:\n  "
                + "\n  ".join(leaked)
                + "\nThese were not rewritten to @loader_path — the wheel will break\n"
                "on machines without these absolute paths (e.g. without Homebrew)."
            )

    def _verify_windows_isolation(self, binary: Path, bin_dir: Path) -> None:
        """Re-run --version with PATH=System32 only.

        Windows DLL search order is: app dir → System32 → SysWOW64 →
        Windows dir → cwd → PATH.  The binary lives in ``bin_dir``, and
        we copy bundled DLLs alongside it, so app-dir resolution covers
        everything we shipped.  Stripping PATH down to ``%SystemRoot%
        \\System32`` lets the OS DLLs in the SYSTEM_LIBS blocklist
        resolve while denying anything from MSYS2, the QEMU installer
        directory, or other PATH entries on the build runner.  A missed
        DLL therefore produces a non-zero exit.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "LD_LIBRARY"))}
        system_root = env.get("SystemRoot", r"C:\Windows")
        env["PATH"] = str(bin_dir) + os.pathsep + str(Path(system_root) / "System32")

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
                f"Isolation verification of {binary.name} timed out (PATH stripped)."
            ) from e

        if result.returncode != 0:
            raise RuntimeError(
                f"Isolation verification of bundled {binary.name} failed:\n"
                f"  Exit code: {result.returncode}\n"
                f"  Stderr: {result.stderr}\n"
                "With PATH=bin_dir;System32 the loader could not find a DLL\n"
                "the binary needs.  bundle_windows_dlls missed it; the wheel\n"
                "would break on hosts without it on PATH."
            )

    def set_platform_wheel_tag(self, build_data: dict, bin_dir: Path | None = None) -> None:
        """Set build_data fields for a platform-specific py3-none wheel.

        On Windows ARM64, overrides the tag from ``win_amd64`` to ``win_arm64``
        when native hardware is ARM64 (Python may report amd64 under emulation).

        On Linux, the manylinux level is derived from the actual glibc symbol
        versions the bundled binaries require (via :func:`_linux_manylinux_tag`)
        rather than hardcoded. The binaries link against the build runner's
        glibc, so the tag must reflect their real floor. A hardcoded
        ``manylinux_2_17`` lets pip install wheels that then fail to load on
        hosts with an older glibc than the runner. ``bin_dir`` (the directory
        holding the bundled binaries) is required for Linux wheels.
        """
        build_data["pure_python"] = False
        platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")

        # Detect native arch on Windows ARM64
        import platform as _platform

        if _platform.system() == "Windows":
            try:
                import winreg

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

        if platform_tag.startswith("linux_"):
            if bin_dir is None:
                raise RuntimeError(
                    "set_platform_wheel_tag requires bin_dir for Linux wheels to "
                    "derive the manylinux tag from the bundled binaries' glibc "
                    "requirement."
                )
            platform_tag = self._linux_manylinux_tag(bin_dir, platform_tag)

        build_data["tag"] = f"py3-none-{platform_tag}"

    def _linux_manylinux_tag(self, bin_dir: Path, platform_tag: str) -> str:
        """Derive the manylinux platform tag from the bundled ELF binaries.

        Scans every ELF file under ``bin_dir`` for the glibc symbol versions it
        references and asks auditwheel which manylinux policy that implies. The
        result (e.g. ``manylinux_2_38_aarch64``) is the lowest manylinux level
        the binaries can actually run on. auditwheel is a hard build dependency
        on Linux; if it or the analysis fails we raise rather than guess, since
        a wrong tag produces wheels that crash on load instead of being
        rejected at install time.
        """
        from collections import defaultdict

        # Linux-only build deps, absent from the dev venv on other platforms.
        from auditwheel.architecture import Architecture  # ty: ignore[unresolved-import]
        from auditwheel.elfutils import elf_find_versioned_symbols  # ty: ignore[unresolved-import]
        from auditwheel.libc import Libc  # ty: ignore[unresolved-import]
        from auditwheel.policy import WheelPolicies  # ty: ignore[unresolved-import]
        from elftools.common.exceptions import ELFError  # ty: ignore[unresolved-import]
        from elftools.elf.elffile import ELFFile  # ty: ignore[unresolved-import]

        arch_name = platform_tag[len("linux_") :]
        try:
            arch = Architecture(arch_name)
        except ValueError as exc:
            raise RuntimeError(
                f"Unknown architecture {arch_name!r} for manylinux tagging."
            ) from exc

        versioned_symbols: dict[str, set[str]] = defaultdict(set)
        elf_count = 0
        for path in sorted(bin_dir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                with path.open("rb") as fh:
                    if fh.read(4) != b"\x7fELF":
                        continue
                    fh.seek(0)
                    elf = ELFFile(fh)
                    for soname, version in elf_find_versioned_symbols(elf):
                        versioned_symbols[soname].add(version)
            except (ELFError, OSError):
                continue
            elf_count += 1

        if elf_count == 0:
            raise RuntimeError(f"No ELF binaries found under {bin_dir} to derive a glibc tag from.")

        policies = WheelPolicies(libc=Libc.GLIBC, arch=arch)
        policy_name = policies.versioned_symbols_policy(dict(versioned_symbols)).name
        if not policy_name.startswith("manylinux_"):
            raise RuntimeError(
                f"Bundled binaries require a glibc newer than any manylinux policy "
                f"auditwheel knows ({policy_name!r}). Upgrade auditwheel or build "
                f"against an older glibc. Required symbol versions: "
                f"{dict(versioned_symbols)}"
            )
        self.app.display_info(f"Derived manylinux tag from glibc usage: {policy_name}")
        return policy_name

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
            raise RuntimeError(
                "patchelf is required to bundle Linux shared libraries.\n"
                "Without it, the wheel ships QEMU with no bundled deps and no\n"
                "RPATH, which only works on hosts that already have every\n"
                "QEMU runtime dep installed system-wide.\n"
                "Install with:  sudo apt-get install patchelf  (or dnf)."
            )

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

        not_found: list[str] = []
        for line in result.stdout.splitlines():
            if "=>" not in line:
                continue

            if "not found" in line:
                not_found.append(line.strip())
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

        if not_found:
            joined = "\n  ".join(not_found)
            raise RuntimeError(
                f"ldd reported unresolved NEEDED libraries for {binary}:\n"
                f"  {joined}\n"
                "The build runner is missing these libs, so they cannot be\n"
                "bundled.  Install the corresponding system packages on the\n"
                "runner before building.  Shipping the wheel without them\n"
                "would silently break on hosts that don't have them."
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
