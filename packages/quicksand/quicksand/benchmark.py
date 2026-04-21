"""Benchmark sandbox boot times.

Usage::

    import asyncio
    from quicksand.benchmark import benchmark

    result = asyncio.run(benchmark("ubuntu", iterations=5))
    print(result)
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time

from pydantic import BaseModel, ConfigDict

from quicksand import BootTiming, Mount, Sandbox, install


class BootSample(BaseModel):
    """A single boot-time measurement."""

    model_config = ConfigDict(frozen=True)

    iteration: int
    boot_time_s: float


class BenchmarkResult(BaseModel):
    """Aggregate result of a boot-time benchmark run."""

    model_config = ConfigDict(frozen=True)

    image: str
    iterations: int
    qemu_command: list[str]
    samples: list[BootSample]
    boot_timing: BootTiming | None
    min_s: float
    p50_s: float
    p90_s: float
    p95_s: float
    p99_s: float
    max_s: float

    def __str__(self) -> str:
        lines = [
            "Quicksand Boot Benchmark",
            f"  Image:       {self.image}",
            f"  Iterations:  {self.iterations}",
            "",
            "QEMU Command:",
            self._format_qemu_command(),
            "",
        ]
        for s in self.samples:
            bar_len = int(min(s.boot_time_s / self.max_s, 1.0) * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  #{s.iteration + 1:<3} {bar} {s.boot_time_s:>7.3f}s")
        lines.append("")
        for label, val in [
            ("min", self.min_s),
            ("p50", self.p50_s),
            ("p90", self.p90_s),
            ("p95", self.p95_s),
            ("p99", self.p99_s),
            ("max", self.max_s),
        ]:
            lines.append(f"  {label:<4} {val:>7.3f}s")
        if self.boot_timing:
            lines.append("")
            lines.append("Boot Phase Breakdown (last run):")
            lines.append(str(self.boot_timing))
        return "\n".join(lines)

    def _format_qemu_command(self) -> str:
        """Format the QEMU command as a readable, arg-per-line shell command."""
        if not self.qemu_command:
            return "  (not captured)"
        parts = list(self.qemu_command)
        result_lines: list[str] = [f"  {parts[0]}"]
        i = 1
        while i < len(parts):
            arg = parts[i]
            if arg.startswith("-") and i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                result_lines.append(f"    {arg} {parts[i + 1]} \\")
                i += 2
            else:
                result_lines.append(f"    {arg} \\")
                i += 1
        # Remove trailing backslash from last line
        if result_lines:
            result_lines[-1] = result_lines[-1].rstrip(" \\")
        return "\n".join(result_lines)


async def benchmark(
    image: str,
    *,
    iterations: int = 5,
    install_extras: list[str] | None = None,
    mounts: list[Mount] | None = None,
) -> BenchmarkResult:
    """Measure boot time over *iterations* runs.

    Args:
        image: Image name to benchmark (e.g. ``"ubuntu"``).
        iterations: Number of boot cycles.
        install_extras: Extras to ``quicksand.install()`` before benchmarking.
            Pass an explicit list to install packages (e.g. ``["qemu", "ubuntu"]``).
            By default nothing is installed, so a system QEMU is used.
        mounts: Optional list of :class:`Mount` specs to include during boot.

    Returns:
        A :class:`BenchmarkResult` with per-iteration samples and summary stats.
    """
    if install_extras:
        install(*install_extras)

    samples: list[BootSample] = []
    qemu_command: list[str] = []
    boot_timing: BootTiming | None = None

    for i in range(iterations):
        _print_progress(i, iterations)

        sandbox = Sandbox(image=image, mounts=mounts or [])
        t0 = time.perf_counter()
        await sandbox.start()
        boot_time = time.perf_counter() - t0

        if not qemu_command:
            qemu_command = sandbox.qemu_command or []
        boot_timing = sandbox.boot_timing

        await sandbox.stop()

        samples.append(
            BootSample(
                iteration=i,
                boot_time_s=round(boot_time, 4),
            )
        )

    _print_progress(iterations, iterations)
    sys.stdout.write("\n")

    times = sorted(s.boot_time_s for s in samples)
    return BenchmarkResult(
        image=image,
        iterations=iterations,
        qemu_command=qemu_command,
        boot_timing=boot_timing,
        samples=samples,
        min_s=times[0],
        p50_s=_percentile(times, 50),
        p90_s=_percentile(times, 90),
        p95_s=_percentile(times, 95),
        p99_s=_percentile(times, 99),
        max_s=times[-1],
    )


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from a sorted list (nearest-rank)."""
    k = (p / 100) * (len(sorted_values) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return round(sorted_values[f] * (c - k) + sorted_values[c] * (k - f), 4)


# ── CLI integration ───────────────────────────────────────────────────


def _print_progress(completed: int, total: int) -> None:
    """Print a cross-platform progress bar matching the install download style."""
    bar_width = 30
    filled = int(bar_width * completed / total) if total > 0 else 0
    bar = "#" * filled + "-" * (bar_width - filled)
    percent = (completed / total * 100) if total > 0 else 0
    sys.stdout.write(f"\r  {bar} {percent:5.1f}% ({completed}/{total} iterations)")
    sys.stdout.flush()


def _parse_mount(value: str) -> Mount:
    """Parse ``HOST:GUEST[:TYPE]`` into a :class:`Mount`."""
    parts = value.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            f"Invalid mount format: {value} (expected HOST:GUEST[:TYPE])"
        )
    host, guest = parts[0], parts[1]
    mount_type = parts[2] if len(parts) > 2 else "cifs"
    if mount_type not in ("cifs", "9p"):
        raise argparse.ArgumentTypeError(f"Invalid mount type: {mount_type} (expected cifs or 9p)")
    from typing import cast

    from quicksand import MountType

    return Mount(host=host, guest=guest, type=cast(MountType, mount_type))


def register_args(parser: argparse.ArgumentParser) -> None:
    """Add benchmark arguments to a parser."""
    parser.add_argument("image", help="Image to benchmark (e.g. ubuntu)")
    parser.add_argument(
        "-n", "--iterations", type=int, default=5, help="Number of boot iterations (default: 5)"
    )
    parser.add_argument(
        "-v",
        "--mount",
        action="append",
        metavar="HOST:GUEST[:TYPE]",
        help="Mount host directory into sandbox during boot (repeatable)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="Output results as JSON"
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the benchmark subcommand."""
    parser = subparsers.add_parser(
        "benchmark",
        help="Benchmark sandbox boot times",
    )
    register_args(parser)


def cmd(args: argparse.Namespace) -> int:
    """Run the benchmark CLI command."""
    mounts = [_parse_mount(m) for m in args.mount] if args.mount else None
    result = asyncio.run(benchmark(args.image, iterations=args.iterations, mounts=mounts))
    if args.json_output:
        print(result.model_dump_json(indent=2))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    import argparse as _argparse

    _parser = _argparse.ArgumentParser(description="Benchmark quicksand boot times")
    register_args(_parser)
    _args = _parser.parse_args()

    _result = asyncio.run(benchmark(_args.image, iterations=_args.iterations))
    print(_result)
