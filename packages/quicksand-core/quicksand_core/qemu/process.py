"""QEMU process lifecycle management.

This module extracts process management logic from sandbox.py to improve
code organization and testability.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path
from typing import IO

from .._types import Timeouts

logger = logging.getLogger("quicksand.process")


class VMProcessManager:
    """Manages QEMU process lifecycle.

    Handles starting, monitoring, and terminating the QEMU process,
    as well as capturing console output for debugging.
    """

    def __init__(self):
        """Initialize the process manager."""
        self._process: subprocess.Popen | None = None
        self._command: list[str] | None = None
        self._console_log: Path | None = None
        self._console_file: IO[str] | None = None

    @property
    def is_running(self) -> bool:
        """Check if the process is still running."""
        return self._process is not None and self._process.poll() is None

    @property
    def process(self) -> subprocess.Popen | None:
        """Get the underlying process object."""
        return self._process

    @property
    def pid(self) -> int | None:
        """Get the process ID, or None if not running."""
        return self._process.pid if self._process else None

    @property
    def returncode(self) -> int | None:
        """Get the return code, or None if still running."""
        return self._process.returncode if self._process else None

    @property
    def command(self) -> list[str] | None:
        """Get the QEMU command that was used to start the process."""
        return list(self._command) if self._command else None

    def start(
        self,
        command: list[str],
        env: dict[str, str],
        console_log_path: Path,
    ) -> None:
        """Start the QEMU process.

        Args:
            command: The QEMU command line arguments.
            env: Environment variables for the process.
            console_log_path: Path to write console output.
        """
        if self._process is not None:
            raise RuntimeError("Process already started")

        self._command = list(command)
        self._console_log = console_log_path
        self._console_file = self._console_log.open("w")

        self._process = subprocess.Popen(
            command,
            stdout=self._console_file,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            env=env,
        )

        logger.debug(f"Started QEMU process with PID {self._process.pid}")

    def write_serial(self, data: bytes) -> None:
        """Write raw bytes to the VM's serial port (stdin of QEMU process).

        Used to send keystrokes to the serial console (e.g., to dismiss
        interactive prompts that appear before autoinstall takes over).
        """
        if self._process and self._process.stdin:
            self._process.stdin.write(data)
            self._process.stdin.flush()

    def get_console_output(self, max_bytes: int = 2000) -> str:
        """Get console output from the guest.

        Flushes any buffered content and returns the last N bytes.

        Args:
            max_bytes: Maximum number of bytes to return.

        Returns:
            Console output string, truncated if necessary.
        """
        if not self._console_log:
            return ""
        try:
            # Flush the console file if it's still open
            if self._console_file is not None:
                self._console_file.flush()
            # Read the last N bytes of the log
            if self._console_log.exists():
                content = self._console_log.read_text()
                return content[-max_bytes:] if len(content) > max_bytes else content
        except Exception as e:
            logger.debug(f"Failed to read console output: {e}")
        return ""

    def get_stderr(self) -> str:
        """Get stderr output from the process."""
        if self._process and self._process.stderr:
            try:
                return self._process.stderr.read().decode()
            except Exception:
                pass
        return ""

    def terminate(
        self,
        graceful_timeout: float = Timeouts.PROCESS_TERMINATE,
    ) -> list[tuple[str, Exception]]:
        """Terminate the QEMU process.

        First attempts graceful termination, then forceful kill if needed.

        Args:
            graceful_timeout: Seconds to wait for graceful termination.

        Returns:
            List of (component, exception) tuples for any errors.
        """
        errors: list[tuple[str, Exception]] = []

        # Close console file
        if self._console_file is not None:
            try:
                self._console_file.close()
            except Exception as e:
                errors.append(("console file", e))
                logger.warning("Failed to close console file: %s", e)
            finally:
                self._console_file = None

        # Terminate process
        if self._process:
            pid = self._process.pid
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=graceful_timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "QEMU process %d did not terminate gracefully, sending SIGKILL", pid
                    )
                    self._process.kill()
                    self._process.wait(timeout=graceful_timeout)
            except Exception as e:
                errors.append(("QEMU process", e))
                logger.error("Failed to terminate QEMU process (pid=%d): %s", pid, e)
                # Try one more kill
                if self._process.poll() is None:
                    with contextlib.suppress(Exception):
                        self._process.kill()
            finally:
                self._process = None

        return errors

    def check_exited(self) -> tuple[bool, str]:
        """Check if the process has exited unexpectedly.

        Returns:
            Tuple of (has_exited, error_message).
        """
        if self._process is None:
            return (True, "Process not started")

        if self._process.poll() is not None:
            stderr = self.get_stderr()
            return (
                True,
                f"VM process exited unexpectedly (exit code: {self._process.returncode}).\n"
                f"QEMU stderr:\n{stderr or '(empty)'}",
            )

        return (False, "")
