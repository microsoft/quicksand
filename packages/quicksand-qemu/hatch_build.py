"""Hatch build hook for platform-specific quicksand-qemu wheels with bundled QEMU."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from quicksand_build_tools import BinaryBundler


def _detect_native_machine() -> str:
    """Detect the native CPU architecture, seeing through Windows emulation.

    On Windows ARM64, Python may run as x86_64 through transparent emulation,
    causing ``platform.machine()`` to return ``"AMD64"``.  The registry key
    ``HKLM\\...\\PROCESSOR_ARCHITECTURE`` always reflects the true hardware.

    Returns a lowercase architecture string (e.g. ``"arm64"``, ``"amd64"``).
    """
    if platform.system() == "Windows":
        try:
            import winreg  # type: ignore[import-not-found]

            key = winreg.OpenKey(  # ty:ignore[unresolved-attribute]
                winreg.HKEY_LOCAL_MACHINE,  # ty:ignore[unresolved-attribute]
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            )
            value, _ = winreg.QueryValueEx(key, "PROCESSOR_ARCHITECTURE")  # ty:ignore[unresolved-attribute]
            winreg.CloseKey(key)  # ty:ignore[unresolved-attribute]
            return value.lower()
        except Exception:
            pass
    return platform.machine().lower()


# macOS entitlements plist for hypervisor access
MACOS_ENTITLEMENTS = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.hypervisor</key>
    <true/>
</dict>
</plist>
"""


class RuntimeBuildHook(BuildHookInterface):
    """Build hook that bundles QEMU binaries into quicksand-qemu."""

    PLUGIN_NAME = "runtime"

    def _bundle_qemu_data_files(self, bin_dir: Path, qemu_binary: str) -> None:
        """Bundle BIOS, boot ROM, and keymap files.

        x86_64 needs BIOS/ROM files. ARM64 doesn't (virt machine loads kernels directly).
        Keymaps are needed on all architectures for VNC display.
        """
        qemu_path = Path(qemu_binary)
        qemu_share_dir_linux = qemu_path.parent.parent / "share" / "qemu"
        qemu_share_dir_windows = qemu_path.parent / "share"

        # Create destination directory
        data_dir = bin_dir / "share" / "qemu"
        data_dir.mkdir(parents=True, exist_ok=True)

        # Bundle keymaps (needed on all architectures for VNC display)
        keymaps_sources = [
            qemu_share_dir_linux / "keymaps",
            qemu_share_dir_windows / "keymaps",
            Path("/usr/share/qemu/keymaps"),
        ]
        for src in keymaps_sources:
            if src.is_dir():
                dest = data_dir / "keymaps"
                shutil.copytree(src, dest, dirs_exist_ok=True)
                self.app.display_info(f"Bundled keymaps from {src}")
                break
        else:
            self.app.display_warning("Keymaps not found — VNC display may not work")

        # BIOS/ROM files only needed for x86_64
        if _detect_native_machine() in ("arm64", "aarch64"):
            return

        # Required files for x86_64 direct kernel boot
        required_files = {
            "bios-256k.bin": [
                Path("/usr/share/seabios/bios-256k.bin"),  # Ubuntu seabios
                qemu_share_dir_windows / "bios-256k.bin",  # Windows (Stefan Weil)
                qemu_share_dir_linux / "bios-256k.bin",  # macOS/Linux
                Path("/usr/share/qemu/bios-256k.bin"),
            ],
            "linuxboot_dma.bin": [
                qemu_share_dir_windows / "linuxboot_dma.bin",  # Windows (Stefan Weil)
                qemu_share_dir_linux / "linuxboot_dma.bin",  # macOS/Linux
                Path("/usr/share/qemu/linuxboot_dma.bin"),
                Path("/usr/lib/ipxe/qemu/linuxboot_dma.bin"),  # Ubuntu ipxe-qemu
            ],
        }

        for filename, search_paths in required_files.items():
            found = False
            for src in search_paths:
                if src.exists():
                    shutil.copy2(src, data_dir / filename)
                    self.app.display_info(f"Bundled: {filename} (from {src})")
                    found = True
                    break

            if not found:
                raise RuntimeError(
                    f"Required file not found: {filename}\n"
                    f"Searched: {[str(p) for p in search_paths]}\n"
                    "Ensure QEMU and seabios/ipxe-qemu are installed."
                )

    def _find_qemu_install_dir(self, qemu_path: Path) -> Path:
        """Find the QEMU installation directory from a binary path."""
        system = platform.system().lower()

        if system == "darwin":
            # Homebrew: /opt/homebrew/Cellar/qemu/10.2.0/bin/qemu-system-*
            return qemu_path.parent.parent

        elif system == "windows":
            # Stefan Weil: C:\Program Files\qemu\qemu-system-*.exe
            return qemu_path.parent

        else:  # Linux
            # apt: /usr/bin/qemu-system-*
            return Path("/usr")

    def _find_qemu_license_files(self, install_dir: Path) -> list[Path]:
        """Find upstream license files for GPL compliance."""
        system = platform.system().lower()
        found: list[Path] = []

        if system == "darwin":
            for filename in ["COPYING", "COPYING.LIB", "LICENSE"]:
                path = install_dir / filename
                if path.exists():
                    found.append(path)

        elif system == "linux":
            doc_dirs = list(Path("/usr/share/doc").glob("qemu-system-*"))
            for doc_dir in doc_dirs:
                copyright_file = doc_dir / "copyright"
                if copyright_file.exists():
                    found.append(copyright_file)
                    break

            for path in [
                Path("/usr/share/doc/qemu-system-common/COPYING"),
                Path("/usr/share/licenses/qemu/COPYING"),
                Path("/usr/share/doc/qemu/COPYING"),
            ]:
                if path.exists():
                    found.append(path)
                    break

        elif system == "windows":
            for pattern in ["COPYING*", "LICENSE*"]:
                found.extend(install_dir.glob(pattern))

        return found

    def _bundle_upstream_licenses(self, bin_dir: Path, qemu_binary: str) -> None:
        """Copy license files from QEMU installation for GPL compliance."""
        qemu_path = Path(qemu_binary).resolve()
        install_dir = self._find_qemu_install_dir(qemu_path)

        license_files = self._find_qemu_license_files(install_dir)
        if not license_files:
            raise RuntimeError(
                f"No GPL license files found for QEMU installation.\n"
                f"QEMU binary: {qemu_binary}\n"
                f"Install dir: {install_dir}\n"
                f"Platform: {platform.system()}\n"
                f"Expected locations:\n"
                f"  macOS: <cellar>/qemu/<version>/COPYING\n"
                f"  Linux: /usr/share/doc/qemu-system-*/copyright\n"
                f"  Windows: <install_dir>/COPYING\n"
                f"GPL compliance requires distributing license files with binaries."
            )

        licenses_dir = bin_dir / "licenses"
        licenses_dir.mkdir(parents=True, exist_ok=True)

        for src in license_files:
            dest = licenses_dir / src.name
            shutil.copy2(src, dest)
            self.app.display_info(f"Copied upstream license: {src.name}")

        self.app.display_info(f"GPL compliance: bundled {len(license_files)} license files")

    # -----------------------------------------------------------------
    # SOURCES.md — source URL resolution for GPL compliance
    # -----------------------------------------------------------------

    def _generate_sources_md(self, bin_dir: Path, qemu_binary: str) -> None:
        """Generate SOURCES.md listing source URLs for every bundled component.

        GPL compliance requires providing (or offering) source code for
        GPL-licensed binaries we redistribute.  This method queries the
        system package manager to resolve each bundled library back to
        its upstream source tarball URL and writes the result into the
        wheel's ``licenses/`` directory.
        """
        system = platform.system().lower()
        qemu_path = Path(qemu_binary).resolve()

        libs = self._collect_bundled_libs(bin_dir, system)
        qemu_ver = self._get_qemu_version(qemu_path)

        if system == "darwin":
            entries = self._resolve_sources_macos(libs, qemu_ver)
        elif system == "linux":
            entries = self._resolve_sources_linux(libs, qemu_ver)
        elif system == "windows":
            entries = self._resolve_sources_windows(bin_dir, qemu_ver)
        else:
            self.app.display_warning(f"Unknown platform {system}, skipping SOURCES.md")
            return

        licenses_dir = bin_dir / "licenses"
        licenses_dir.mkdir(parents=True, exist_ok=True)
        self._write_sources_md(licenses_dir / "SOURCES.md", entries, qemu_ver, system)
        self.app.display_info(f"Generated SOURCES.md with {len(entries)} component(s)")

    @staticmethod
    def _get_qemu_version(binary: Path) -> str:
        """Extract the QEMU version string from a binary."""
        data = binary.read_bytes()
        m = re.search(rb"QEMU emulator version (\d+\.\d+\.\d+)", data)
        if m:
            return m.group(1).decode()
        m = re.search(rb"version (\d+\.\d+\.\d+)", data)
        if m:
            return m.group(1).decode()
        return "unknown"

    @staticmethod
    def _collect_bundled_libs(bin_dir: Path, system: str) -> list[Path]:
        """Return paths of all bundled native libraries."""
        libs: list[Path] = []
        lib_dir = bin_dir / "lib"
        if lib_dir.exists():
            for f in sorted(lib_dir.rglob("*")):
                if not f.is_file():
                    continue
                name = f.name
                if name.endswith(".dylib") or ".so." in name or name.endswith(".so"):
                    # Skip QEMU's own accelerator modules
                    try:
                        if f.relative_to(lib_dir).parts[0] == "qemu":
                            continue
                    except (ValueError, IndexError):
                        pass
                    libs.append(f)
        # Windows: DLLs sit directly in bin_dir
        if system == "windows":
            for f in sorted(bin_dir.iterdir()):
                if f.is_file() and f.suffix.lower() == ".dll":
                    libs.append(f)
        return libs

    # -- macOS (Homebrew) --------------------------------------------------

    def _resolve_sources_macos(self, libs: list[Path], qemu_ver: str) -> list[dict[str, str]]:
        """Resolve source URLs via QEMU's Homebrew dependency tree.

        The bundled dylibs are copies (not symlinks), so we cannot
        reverse-map them to the Cellar.  Instead we query ``brew info
        --json=v2 qemu`` which includes the full recursive dependency
        list, then fetch metadata for each dependency.
        """
        entries: list[dict[str, str]] = []

        # QEMU itself
        qemu_info = self._brew_formula_info("qemu")
        entries.append(
            {
                "name": "QEMU",
                "version": qemu_info["version"] if qemu_info else qemu_ver,
                "license": "GPL-2.0-only",
                "source": qemu_info["source"]
                if qemu_info
                else f"https://download.qemu.org/qemu-{qemu_ver}.tar.xz",
                "homepage": "https://www.qemu.org/",
            }
        )

        # Resolve every runtime dependency of the qemu formula
        deps = self._brew_runtime_deps("qemu")
        for dep in deps:
            info = self._brew_formula_info(dep)
            if info:
                entries.append(info)

        return entries

    @staticmethod
    def _brew_runtime_deps(formula: str) -> list[str]:
        """Return the recursive runtime dependency names for a formula."""
        try:
            result = subprocess.run(
                ["brew", "deps", "--installed", formula],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return [d.strip() for d in result.stdout.strip().splitlines() if d.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return []

    @staticmethod
    def _brew_formula_info(formula: str) -> dict[str, str] | None:
        """Get name, version, license, source URL and homepage for a formula."""
        try:
            result = subprocess.run(
                ["brew", "info", "--json=v2", formula],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            f = data["formulae"][0]
            return {
                "name": f["name"],
                "version": f["versions"]["stable"],
                "license": f.get("license") or "Unknown",
                "source": f["urls"]["stable"]["url"],
                "homepage": f.get("homepage", ""),
            }
        except (
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            KeyError,
            IndexError,
        ):
            return None

    # -- Linux (dpkg / rpm) ------------------------------------------------

    def _resolve_sources_linux(self, libs: list[Path], qemu_ver: str) -> list[dict[str, str]]:
        """Resolve source URLs via ``dpkg -S`` + ``apt-cache show`` (or rpm)."""
        entries: list[dict[str, str]] = []

        # QEMU itself
        qemu_src = self._apt_source_url("qemu-system-x86") or self._apt_source_url(
            "qemu-system-arm"
        )
        entries.append(
            {
                "name": "QEMU",
                "version": qemu_ver,
                "license": "GPL-2.0-only",
                "source": qemu_src or f"https://download.qemu.org/qemu-{qemu_ver}.tar.xz",
                "homepage": "https://www.qemu.org/",
            }
        )

        # SeaBIOS (x86_64 only)
        seabios_src = self._apt_source_url("seabios")
        if seabios_src:
            seabios_ver = self._dpkg_version("seabios") or "unknown"
            entries.append(
                {
                    "name": "SeaBIOS",
                    "version": seabios_ver,
                    "license": "LGPL-3.0-or-later",
                    "source": seabios_src,
                    "homepage": "https://www.seabios.org/",
                }
            )

        seen_pkgs: set[str] = set()
        for lib in libs:
            pkg = self._dpkg_package_for_lib(lib)
            if not pkg:
                pkg = self._rpm_package_for_lib(lib)
            if pkg and pkg not in seen_pkgs:
                seen_pkgs.add(pkg)
                info = self._linux_package_info(pkg)
                if info:
                    entries.append(info)

        return entries

    @staticmethod
    def _dpkg_package_for_lib(lib: Path) -> str | None:
        """Find the owning Debian package for a library file."""
        # The library was copied into the wheel; find the original system path.
        # dpkg -S searches by filename pattern.
        try:
            result = subprocess.run(
                ["dpkg", "-S", lib.name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Output: "libfoo1:amd64: /usr/lib/x86_64-linux-gnu/libfoo.so.1"
                first_line = result.stdout.strip().splitlines()[0]
                return first_line.split(":")[0]
        except (subprocess.TimeoutExpired, IndexError, FileNotFoundError):
            pass
        return None

    @staticmethod
    def _rpm_package_for_lib(lib: Path) -> str | None:
        """Find the owning RPM package for a library file (Fedora/RHEL)."""
        try:
            result = subprocess.run(
                ["rpm", "-qf", "--qf", "%{NAME}", f"/usr/lib64/{lib.name}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    @staticmethod
    def _dpkg_version(pkg: str) -> str | None:
        """Get the installed version of a Debian package."""
        try:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Version}", pkg],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    @staticmethod
    def _apt_source_url(pkg: str) -> str | None:
        """Get the upstream homepage / source URL from apt-cache."""
        try:
            result = subprocess.run(
                ["apt-cache", "show", pkg],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("Homepage:"):
                        return line.split(":", 1)[1].strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def _linux_package_info(self, pkg: str) -> dict[str, str] | None:
        """Assemble source info for a Linux package."""
        version = self._dpkg_version(pkg) or "unknown"
        homepage = self._apt_source_url(pkg) or ""

        # Build a source-package URL for Debian/Ubuntu
        src_pkg = self._dpkg_source_package(pkg)
        if src_pkg:
            source = f"https://tracker.debian.org/pkg/{src_pkg}" if homepage == "" else homepage
        else:
            source = homepage or f"https://packages.debian.org/{pkg}"

        return {
            "name": pkg,
            "version": version,
            "license": self._dpkg_license(pkg),
            "source": source,
            "homepage": homepage,
        }

    @staticmethod
    def _dpkg_source_package(pkg: str) -> str | None:
        """Get the source package name for a binary package."""
        try:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f", "${source:Package}", pkg],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    @staticmethod
    def _dpkg_license(pkg: str) -> str:
        """Extract the license from a Debian copyright file (best-effort)."""
        copyright_path = Path(f"/usr/share/doc/{pkg}/copyright")
        if not copyright_path.exists():
            return "Unknown"
        try:
            text = copyright_path.read_text(errors="replace")[:4096]
            # Look for SPDX-style or common license references
            for pattern, spdx in [
                (r"Apache.*2\.0", "Apache-2.0"),
                (r"GPL-3", "GPL-3.0-or-later"),
                (r"GPL-2", "GPL-2.0-or-later"),
                (r"LGPL-3", "LGPL-3.0-or-later"),
                (r"LGPL-2\.1", "LGPL-2.1-or-later"),
                (r"LGPL-2", "LGPL-2.0-or-later"),
                (r"MIT", "MIT"),
                (r"BSD-3", "BSD-3-Clause"),
                (r"BSD-2", "BSD-2-Clause"),
                (r"MPL-2", "MPL-2.0"),
                (r"Zlib", "Zlib"),
            ]:
                if re.search(pattern, text):
                    return spdx
        except OSError:
            pass
        return "Unknown"

    # -- Windows (Stefan Weil QEMU distribution) ---------------------------

    def _resolve_sources_windows(self, bin_dir: Path, qemu_ver: str) -> list[dict[str, str]]:
        """Resolve sources for the Stefan Weil / MSYS2 QEMU distribution."""
        entries: list[dict[str, str]] = []

        entries.append(
            {
                "name": "QEMU",
                "version": qemu_ver,
                "license": "GPL-2.0-only",
                "source": f"https://download.qemu.org/qemu-{qemu_ver.rstrip('.90')}.tar.xz"
                if ".90" not in qemu_ver
                else "https://gitlab.com/qemu-project/qemu (development snapshot)",
                "homepage": "https://www.qemu.org/",
            }
        )

        # Windows QEMU distributions bundle MSYS2/MinGW packages.
        # Map DLL filenames to MSYS2 package names and upstream projects.
        dll_to_upstream = self._windows_dll_upstream_map()

        seen: set[str] = set()
        for f in sorted(bin_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".dll":
                continue
            name = f.stem.lower()
            # Normalize: strip lib prefix and version suffix
            key = re.sub(r"^lib", "", name)
            key = re.sub(r"[-_]?\d[\d.]*$", "", key)

            info = dll_to_upstream.get(key)
            if not info:
                # Try the full stem
                info = dll_to_upstream.get(name)
            if not info:
                # Try with lib prefix
                info = dll_to_upstream.get(f"lib{key}")

            if info and info["name"] not in seen:
                seen.add(info["name"])
                entries.append(info)

        return entries

    @staticmethod
    def _windows_dll_upstream_map() -> dict[str, dict[str, str]]:
        """Mapping of Windows DLL basenames to upstream source metadata.

        The Windows QEMU build from qemu.weilnetz.de bundles MSYS2/MinGW
        libraries.  Since there is no package manager to query at build
        time on Windows CI, we maintain a static mapping here.
        """

        # Helper to reduce repetition
        def _e(name: str, license: str, source: str, homepage: str = "") -> dict[str, str]:
            return {
                "name": name,
                "license": license,
                "source": source,
                "homepage": homepage or source,
            }

        gnu_mirror = "https://ftp.gnu.org/gnu"
        return {
            # Core
            "capstone": _e(
                "Capstone", "BSD-3-Clause", "https://github.com/capstone-engine/capstone"
            ),
            "fdt": _e("dtc (libfdt)", "BSD-2-Clause", "https://github.com/dgibson/dtc"),
            "ffi": _e("libffi", "MIT", "https://github.com/libffi/libffi"),
            "pixman": _e("Pixman", "MIT", "https://gitlab.freedesktop.org/pixman/pixman"),
            "slirp": _e(
                "libslirp", "BSD-3-Clause", "https://gitlab.freedesktop.org/slirp/libslirp"
            ),
            # TLS / crypto
            "crypto": _e("OpenSSL", "Apache-2.0", "https://github.com/openssl/openssl"),
            "ssl": _e("OpenSSL", "Apache-2.0", "https://github.com/openssl/openssl"),
            "gnutls": _e("GnuTLS", "LGPL-2.1-or-later", "https://www.gnutls.org/"),
            "nettle": _e("Nettle", "LGPL-3.0-or-later", f"{gnu_mirror}/nettle/"),
            "hogweed": _e("Nettle", "LGPL-3.0-or-later", f"{gnu_mirror}/nettle/"),
            "tasn1": _e("libtasn1", "LGPL-2.1-or-later", f"{gnu_mirror}/libtasn1/"),
            "p11-kit": _e("p11-kit", "BSD-3-Clause", "https://github.com/p11-glue/p11-kit"),
            "nss3": _e("NSS", "MPL-2.0", "https://firefox-source-docs.mozilla.org/security/nss/"),
            "nspr4": _e("NSPR", "MPL-2.0", "https://firefox-source-docs.mozilla.org/nspr/"),
            # GLib
            "glib": _e("GLib", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/glib"),
            "gio": _e("GLib", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/glib"),
            "gobject": _e("GLib", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/glib"),
            "gmodule": _e("GLib", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/glib"),
            "gmp": _e("GMP", "LGPL-3.0-or-later", f"{gnu_mirror}/gmp/"),
            "pcre2": _e("PCRE2", "BSD-3-Clause", "https://github.com/PCRE2Project/pcre2"),
            "intl": _e("gettext", "LGPL-2.1-or-later", f"{gnu_mirror}/gettext/"),
            "iconv": _e("libiconv", "LGPL-2.1-or-later", f"{gnu_mirror}/libiconv/"),
            # Compression
            "z": _e("zlib", "Zlib", "https://github.com/madler/zlib"),
            "zlib1": _e("zlib", "Zlib", "https://github.com/madler/zlib"),
            "zstd": _e("Zstandard", "BSD-3-Clause", "https://github.com/facebook/zstd"),
            "lzma": _e("XZ Utils", "0BSD", "https://github.com/tukaani-project/xz"),
            "lzo2": _e("LZO", "GPL-2.0-or-later", "http://www.oberhumer.com/opensource/lzo/"),
            "snappy": _e("Snappy", "BSD-3-Clause", "https://github.com/google/snappy"),
            "bz2": _e("bzip2", "bzip2-1.0.6", "https://sourceware.org/bzip2/"),
            "deflate": _e("libdeflate", "MIT", "https://github.com/ebiggers/libdeflate"),
            "lz4": _e("LZ4", "BSD-2-Clause", "https://github.com/lz4/lz4"),
            "brotlicommon": _e("Brotli", "MIT", "https://github.com/google/brotli"),
            "brotlidec": _e("Brotli", "MIT", "https://github.com/google/brotli"),
            "brotlienc": _e("Brotli", "MIT", "https://github.com/google/brotli"),
            # Image / pixel
            "png16": _e("libpng", "Zlib", "https://github.com/pnggroup/libpng"),
            "jpeg": _e(
                "libjpeg-turbo",
                "IJG AND BSD-3-Clause AND Zlib",
                "https://github.com/libjpeg-turbo/libjpeg-turbo",
            ),
            "tiff": _e("libtiff", "libtiff", "https://gitlab.com/libtiff/libtiff"),
            "webp": _e("libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"),
            "webpdemux": _e(
                "libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"
            ),
            "sharpyuv": _e(
                "libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp"
            ),
            "jxl": _e("JPEG XL", "BSD-3-Clause", "https://github.com/libjxl/libjxl"),
            "jxl_cms": _e("JPEG XL", "BSD-3-Clause", "https://github.com/libjxl/libjxl"),
            "avif": _e("libavif", "BSD-2-Clause", "https://github.com/AOMediaCodec/libavif"),
            "aom": _e("libaom", "BSD-2-Clause", "https://aomedia.googlesource.com/aom/"),
            "dav1d": _e("dav1d", "BSD-2-Clause", "https://code.videolan.org/videolan/dav1d"),
            "svtav1enc": _e("SVT-AV1", "BSD-3-Clause", "https://gitlab.com/AOMediaCodec/SVT-AV1"),
            "rav1e": _e("rav1e", "BSD-2-Clause", "https://github.com/xiph/rav1e"),
            "yuv": _e("libyuv", "BSD-3-Clause", "https://chromium.googlesource.com/libyuv/libyuv"),
            "hwy": _e("Highway", "Apache-2.0", "https://github.com/google/highway"),
            "lerc": _e("LERC", "Apache-2.0", "https://github.com/Esri/lerc"),
            "lcms2": _e("Little CMS 2", "MIT", "https://github.com/mm2/Little-CMS"),
            "jbig": _e("JBIG-KIT", "GPL-2.0-or-later", "https://www.cl.cam.ac.uk/~mgk25/jbigkit/"),
            # Networking
            "idn2": _e("libidn2", "LGPL-3.0-or-later", f"{gnu_mirror}/libidn/"),
            "unistring": _e("libunistring", "LGPL-3.0-or-later", f"{gnu_mirror}/libunistring/"),
            "ssh": _e("libssh", "LGPL-2.1-or-later", "https://www.libssh.org/"),
            "ssh2": _e("libssh2", "BSD-3-Clause", "https://github.com/libssh2/libssh2"),
            "curl": _e("curl", "MIT", "https://github.com/curl/curl"),
            "psl": _e("libpsl", "MIT", "https://github.com/nicjansma/libpsl"),
            "sasl2": _e("Cyrus SASL", "BSD-4-Clause-UC", "https://github.com/cyrusimap/cyrus-sasl"),
            "nfs": _e("libnfs", "LGPL-2.1-or-later", "https://github.com/sahlberg/libnfs"),
            # GUI
            "sdl2": _e("SDL2", "Zlib", "https://github.com/libsdl-org/SDL"),
            "sdl2_image": _e("SDL2_image", "Zlib", "https://github.com/libsdl-org/SDL_image"),
            "gtk": _e("GTK 3", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/gtk"),
            "gdk": _e("GTK 3", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/gtk"),
            "gdk_pixbuf": _e(
                "gdk-pixbuf", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/gdk-pixbuf"
            ),
            "cairo": _e("Cairo", "LGPL-2.1-or-later", "https://www.cairographics.org/"),
            "pango": _e("Pango", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/pango"),
            "pangocairo": _e("Pango", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/pango"),
            "pangoft2": _e("Pango", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/pango"),
            "pangowin32": _e("Pango", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/pango"),
            "harfbuzz": _e("HarfBuzz", "MIT", "https://github.com/harfbuzz/harfbuzz"),
            "freetype": _e("FreeType", "FTL", "https://gitlab.freedesktop.org/freetype/freetype"),
            "fontconfig": _e(
                "Fontconfig", "MIT", "https://gitlab.freedesktop.org/fontconfig/fontconfig"
            ),
            "fribidi": _e("FriBidi", "LGPL-2.1-or-later", "https://github.com/fribidi/fribidi"),
            "graphite2": _e(
                "Graphite2", "LGPL-2.1-or-later", "https://github.com/nicjansma/graphite"
            ),
            "atk": _e("ATK", "LGPL-2.1-or-later", "https://gitlab.gnome.org/GNOME/atk"),
            "epoxy": _e("libepoxy", "MIT", "https://github.com/anholt/libepoxy"),
            "expat": _e("Expat", "MIT", "https://github.com/libexpat/libexpat"),
            "datrie": _e("libdatrie", "LGPL-2.1-or-later", "https://github.com/tlwg/libdatrie"),
            "thai": _e("libthai", "LGPL-2.1-or-later", "https://github.com/tlwg/libthai"),
            # GStreamer
            "gstreamer": _e(
                "GStreamer",
                "LGPL-2.1-or-later",
                "https://gitlab.freedesktop.org/gstreamer/gstreamer",
            ),
            "gstbase": _e(
                "GStreamer",
                "LGPL-2.1-or-later",
                "https://gitlab.freedesktop.org/gstreamer/gstreamer",
            ),
            "gstapp": _e(
                "GStreamer",
                "LGPL-2.1-or-later",
                "https://gitlab.freedesktop.org/gstreamer/gstreamer",
            ),
            # Audio
            "opus": _e("Opus", "BSD-3-Clause", "https://github.com/xiph/opus"),
            "orc": _e("ORC", "BSD-3-Clause", "https://gitlab.freedesktop.org/gstreamer/orc"),
            "jack64": _e("JACK Audio", "LGPL-2.1-or-later", "https://github.com/jackaudio/jack2"),
            # Virtualization
            "spice-server": _e(
                "SPICE", "LGPL-2.1-or-later", "https://gitlab.freedesktop.org/spice/spice"
            ),
            "virglrenderer": _e(
                "virglrenderer", "MIT", "https://gitlab.freedesktop.org/virgl/virglrenderer"
            ),
            "usbredirparser": _e(
                "usbredir", "LGPL-2.1-or-later", "https://gitlab.freedesktop.org/spice/usbredir"
            ),
            "u2f-emu": _e(
                "u2f-emulated", "LGPL-2.1-or-later", "https://github.com/nicjansma/u2f-emulated"
            ),
            "brlapi": _e("BRLTTY", "LGPL-2.1-or-later", "https://github.com/brltty/brltty"),
            "cacard": _e(
                "libcacard", "LGPL-2.1-or-later", "https://gitlab.freedesktop.org/spice/libcacard"
            ),
            "usb": _e("libusb", "LGPL-2.1-or-later", "https://github.com/libusb/libusb"),
            # MinGW runtime
            "gcc_s_seh": _e(
                "GCC (runtime)", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org/"
            ),
            "stdc++": _e(
                "GCC (libstdc++)", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org/"
            ),
            "winpthread": _e("mingw-w64", "MIT", "https://github.com/mingw-w64/mingw-w64"),
            "ssp": _e(
                "GCC (libssp)", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org/"
            ),
            "systre": _e("mingw-w64", "MIT", "https://github.com/mingw-w64/mingw-w64"),
            "tre": _e("TRE", "BSD-2-Clause", "https://github.com/laurikari/tre"),
            "db": _e(
                "Berkeley DB",
                "Sleepycat",
                "https://www.oracle.com/database/technologies/related/berkeleydb.html",
            ),
            "ncursesw": _e("ncurses", "X11", f"{gnu_mirror}/ncurses/"),
            # Misc
            "plc4": _e("NSPR", "MPL-2.0", "https://firefox-source-docs.mozilla.org/nspr/"),
            "plds4": _e("NSPR", "MPL-2.0", "https://firefox-source-docs.mozilla.org/nspr/"),
        }

    # -- Write SOURCES.md --------------------------------------------------

    @staticmethod
    def _write_sources_md(
        path: Path,
        entries: list[dict[str, str]],
        qemu_ver: str,
        system: str,
    ) -> None:
        """Write a Markdown file listing all bundled components and their sources."""
        lines = [
            "# Source Code Availability",
            "",
            "This wheel bundles QEMU and its runtime dependencies as pre-built",
            "binaries.  Several of these components are licensed under the GNU",
            "GPL or LGPL, which requires that source code be made available.",
            "",
            "The table below lists every bundled component, its license, and",
            "where to obtain the corresponding source code.",
            "",
            "| Component | Version | License | Source |",
            "|-----------|---------|---------|--------|",
        ]

        for e in entries:
            version = e.get("version", "")
            source = e["source"]
            lines.append(f"| {e['name']} | {version} | {e['license']} | {source} |")

        lines.append("")
        lines.append("If you have questions about source availability, please open an issue at")
        lines.append("https://github.com/microsoft/quicksand.")
        lines.append("")

        path.write_text("\n".join(lines))

    def _bundle_qemu_modules(self, bin_dir: Path, qemu_binary: str) -> None:
        """Bundle QEMU accelerator modules (Linux only)."""
        if platform.system() != "Linux":
            return

        qemu_path = Path(qemu_binary)

        module_search_paths = [
            qemu_path.parent.parent / "lib" / "qemu",
            qemu_path.parent.parent / "lib" / "x86_64-linux-gnu" / "qemu",
            qemu_path.parent.parent / "libexec" / "qemu",
            Path("/usr/lib/qemu"),
            Path("/usr/lib/x86_64-linux-gnu/qemu"),
            Path("/usr/lib/aarch64-linux-gnu/qemu"),
            Path("/usr/libexec/qemu"),
        ]

        source_dir = None
        for path in module_search_paths:
            if path.exists() and list(path.glob("*.so")):
                source_dir = path
                break

        if not source_dir:
            self.app.display_warning("QEMU module directory not found, skipping module bundling")
            return

        module_dir = bin_dir / "lib" / "qemu"
        module_dir.mkdir(parents=True, exist_ok=True)

        accel_patterns = ["accel-*.so"]
        count = 0
        for pattern in accel_patterns:
            for module in source_dir.glob(pattern):
                shutil.copy2(module, module_dir / module.name)
                self.app.display_info(f"Bundled module: {module.name}")
                count += 1

        if count == 0:
            self.app.display_warning(
                f"No accelerator modules found in {source_dir}. TCG may be built-in on this system."
            )

    # -----------------------------------------------------------------
    # QEMU installation
    # NOTE: This logic is shared with quicksand_core.qemu.installer.
    # Keep both in sync when updating installers or package names.
    # -----------------------------------------------------------------

    # Stefan Weil Windows installer — update URL when upgrading QEMU.
    _WINDOWS_QEMU_INSTALLER = "qemu-w64-setup-20260324.exe"
    _WINDOWS_QEMU_URL = f"https://qemu.weilnetz.de/w64/2026/{_WINDOWS_QEMU_INSTALLER}"

    _WINDOWS_ARM64_QEMU_INSTALLER = "qemu-arm-setup-20260401.exe"
    _WINDOWS_ARM64_QEMU_URL = f"https://qemu.weilnetz.de/aarch64/{_WINDOWS_ARM64_QEMU_INSTALLER}"

    def _ensure_qemu_installed(self, qemu_name: str) -> None:
        """Install QEMU if it is not already on PATH.

        Supports macOS (Homebrew), Linux (apt), and Windows (Stefan Weil
        installer).  On Linux the build is expected to run as root (or
        with passwordless sudo) inside a CI runner container.
        """
        if shutil.which(qemu_name):
            self.app.display_info(f"Found {qemu_name} on PATH")
            return

        system = platform.system().lower()
        self.app.display_info(f"QEMU not found — installing for {system}...")

        if system == "darwin":
            self._install_qemu_macos()
        elif system == "linux":
            self._install_qemu_linux()
        elif system == "windows":
            self._install_qemu_windows()
        else:
            raise RuntimeError(f"Unsupported platform for automatic QEMU install: {system}")

    def _install_qemu_macos(self) -> None:
        """Install QEMU via Homebrew."""
        if not shutil.which("brew"):
            raise RuntimeError(
                "Homebrew is required to install QEMU on macOS.\nInstall it from https://brew.sh"
            )
        subprocess.run(["brew", "install", "qemu"], check=True)
        self.app.display_info("Installed QEMU via Homebrew")

    def _install_qemu_linux(self) -> None:
        """Install QEMU via apt (Debian/Ubuntu)."""
        machine = _detect_native_machine()
        pkg = "qemu-system-x86" if machine in ("x86_64", "amd64") else "qemu-system-arm"

        if shutil.which("apt-get"):
            env = {**dict(os.environ), "DEBIAN_FRONTEND": "noninteractive"}
            subprocess.run(
                ["sudo", "apt-get", "update", "-qq"],
                check=True,
                env=env,
            )
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "-qq", pkg, "qemu-utils"],
                check=True,
                env=env,
            )
            self.app.display_info(f"Installed {pkg} and qemu-utils via apt")
        elif shutil.which("dnf"):
            subprocess.run(
                ["sudo", "dnf", "install", "-y", "qemu-system-x86-core", "qemu-img"],
                check=True,
            )
            self.app.display_info("Installed QEMU via dnf")
        else:
            raise RuntimeError(
                "No supported package manager found (apt-get or dnf).\n"
                "Please install QEMU manually."
            )

    def _install_qemu_windows(self) -> None:
        """Install QEMU via the Stefan Weil Windows installer."""
        import urllib.request

        machine = _detect_native_machine()
        if machine in ("arm64", "aarch64"):
            installer_name = self._WINDOWS_ARM64_QEMU_INSTALLER
            installer_url = self._WINDOWS_ARM64_QEMU_URL
        else:
            installer_name = self._WINDOWS_QEMU_INSTALLER
            installer_url = self._WINDOWS_QEMU_URL

        installer = Path(installer_name)
        if not installer.exists():
            self.app.display_info(f"Downloading {installer_url}...")
            urllib.request.urlretrieve(installer_url, installer)

        # Install to a known directory (avoids needing admin for Program Files)
        qemu_dir = Path(os.environ.get("TEMP", r"C:\Temp")) / "qemu"
        self.app.display_info(f"Running QEMU installer (silent) to {qemu_dir}...")
        subprocess.run([str(installer), "/S", f"/D={qemu_dir}"], check=True)

        # Fall back to default location if /D= was ignored
        if not qemu_dir.exists():
            qemu_dir = Path(r"C:\Program Files\qemu")

        if qemu_dir.exists():
            os.environ["PATH"] = str(qemu_dir) + os.pathsep + os.environ.get("PATH", "")
            self.app.display_info(f"Installed QEMU to {qemu_dir}")
        else:
            raise RuntimeError(
                "QEMU installer completed but qemu directory not found.\n"
                "The installer may have failed silently."
            )

    def initialize(self, version: str, build_data: dict) -> None:
        """Bundle QEMU binaries into the package."""
        if self.target_name != "wheel" or version == "editable":
            return

        bundler = BinaryBundler(self.app)

        # Detect architecture
        machine = _detect_native_machine()
        if machine in ("arm64", "aarch64"):
            qemu_name = "qemu-system-aarch64"
        else:
            qemu_name = "qemu-system-x86_64"

        # Install QEMU if not present, then locate it
        self._ensure_qemu_installed(qemu_name)
        qemu_binary = shutil.which(qemu_name)
        qemu_img = shutil.which("qemu-img")

        if not qemu_binary or not qemu_img:
            raise RuntimeError(
                f"QEMU not found after installation attempt.\n"
                f"Looked for: {qemu_name}, qemu-img\n"
                f"Platform: {platform.system()}, arch: {machine}\n"
                "Please install QEMU manually and ensure it is on PATH."
            )

        # Copy binaries to package
        bin_dir = Path(self.root) / "quicksand_qemu" / "bin"
        bin_dir.mkdir(exist_ok=True)

        dest_qemu = bin_dir / Path(qemu_binary).name
        dest_img = bin_dir / Path(qemu_img).name

        # Use shutil.copy (not copy2) to avoid PermissionError from macOS SIP
        # flags on Homebrew binaries
        shutil.copy(qemu_binary, dest_qemu)
        shutil.copy(qemu_img, dest_img)

        # Bundle platform-specific dependencies
        system = platform.system().lower()
        if system == "darwin":
            bundler.bundle_macos_dylibs(dest_qemu, bin_dir, entitlements_plist=MACOS_ENTITLEMENTS)
            bundler.bundle_macos_dylibs(dest_img, bin_dir)
        elif system == "linux":
            bundler.bundle_linux_libs(dest_qemu, bin_dir)
            bundler.bundle_linux_libs(dest_img, bin_dir)
        elif system == "windows":
            bundler.bundle_windows_dlls(Path(qemu_binary), bin_dir)
            bundler.bundle_windows_dlls(Path(qemu_img), bin_dir)

        bundler.make_executable(bin_dir)
        self.app.display_info(f"Bundled QEMU: {dest_qemu.name}")

        # QEMU-specific data files
        self._bundle_qemu_data_files(bin_dir, qemu_binary)
        self._bundle_qemu_modules(bin_dir, qemu_binary)
        self._bundle_upstream_licenses(bin_dir, qemu_binary)
        self._generate_sources_md(bin_dir, qemu_binary)

        # Verify bundled binaries work
        self.app.display_info("Verifying qemu-system...")
        bundler.verify(dest_qemu, bin_dir)
        self.app.display_info("Verifying qemu-img...")
        bundler.verify(dest_img, bin_dir)

        # Force include all files in bin/ directory
        bundler.force_include_bin_dir(bin_dir, Path(self.root), build_data)

        # Mark as platform-specific wheel
        if version != "editable":
            bundler.set_platform_wheel_tag(build_data)
