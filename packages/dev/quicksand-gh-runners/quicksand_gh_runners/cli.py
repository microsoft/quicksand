"""CLI to manage persistent self-hosted GitHub Actions runner VMs."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta, timezone

from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient

from . import config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_compute_client() -> ComputeManagementClient:
    if not config.SUBSCRIPTION_ID:
        print("Error: AZURE_SUBSCRIPTION_ID is not set")
        sys.exit(1)
    return ComputeManagementClient(DefaultAzureCredential(), config.SUBSCRIPTION_ID)


def _resolve_runners(name: str | None) -> dict[str, str]:
    if name is None:
        return config.RUNNERS
    if name not in config.RUNNERS:
        print(f"Unknown runner: {name!r}. Choose from: {', '.join(config.RUNNERS)}")
        sys.exit(1)
    return {name: config.RUNNERS[name]}


def _vm_power_state(client: ComputeManagementClient, vm_name: str) -> str:
    view = client.virtual_machines.instance_view(config.RESOURCE_GROUP, vm_name)
    for status in view.statuses or []:
        code = status.code or ""
        if code.startswith("PowerState/"):
            return code.split("/", 1)[1]
    return "unknown"


def _get_shutdown_time(vm_name: str) -> str | None:
    result = subprocess.run(
        [
            "az",
            "resource",
            "show",
            "--resource-group",
            config.RESOURCE_GROUP,
            "--resource-type",
            "Microsoft.DevTestLab/schedules",
            "--name",
            f"shutdown-computevm-{vm_name}",
            "--query",
            "properties.dailyRecurrence.time",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        utc_time = result.stdout.strip()
        utc_hour = int(utc_time[:2])
        est_hour = (utc_hour - 5) % 24
        period = "am" if est_hour < 12 else "pm"
        display_hour = est_hour % 12 or 12
        return f"{display_hour}{period} EST"
    return None


def _parse_time(time_str: str) -> str:
    est = timezone(timedelta(hours=-5))

    match = re.match(r"^(\d{1,2})(am|pm)$", time_str.lower())
    if match:
        hour = int(match.group(1))
        if match.group(2) == "pm" and hour != 12:
            hour += 12
        elif match.group(2) == "am" and hour == 12:
            hour = 0
        est_dt = datetime.now(est).replace(hour=hour, minute=0, second=0)
        utc_dt = est_dt.astimezone(UTC)
        return f"{utc_dt.hour:02d}{utc_dt.minute:02d}"

    match = re.match(r"^(\d{2}):?(\d{2})$", time_str)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    print(f"Cannot parse time: {time_str!r}. Use e.g. '10pm', '11pm', '22:00'")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> None:
    runners = _resolve_runners(args.runner)
    client = _get_compute_client()

    def start_one(alias: str, vm_name: str) -> str:
        client.virtual_machines.begin_start(config.RESOURCE_GROUP, vm_name).result()
        return f"  {alias}: started"

    print("Starting runners...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(start_one, a, v): a for a, v in runners.items()}
        for f in as_completed(futures):
            print(f.result())


def cmd_stop(args: argparse.Namespace) -> None:
    runners = _resolve_runners(args.runner)
    client = _get_compute_client()

    def stop_one(alias: str, vm_name: str) -> str:
        client.virtual_machines.begin_deallocate(config.RESOURCE_GROUP, vm_name).result()
        return f"  {alias}: deallocated"

    print("Stopping runners...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(stop_one, a, v): a for a, v in runners.items()}
        for f in as_completed(futures):
            print(f.result())


def cmd_status(args: argparse.Namespace) -> None:
    runners = _resolve_runners(args.runner)
    client = _get_compute_client()

    for alias, vm_name in runners.items():
        try:
            state = _vm_power_state(client, vm_name)
            shutdown = _get_shutdown_time(vm_name)
            shutdown_str = f"  (shutdown: {shutdown})" if shutdown else ""
            print(f"  {alias:6s} {vm_name:30s} {state}{shutdown_str}")
        except Exception as e:
            print(f"  {alias:6s} {vm_name:30s} error: {e}")


def cmd_extend(args: argparse.Namespace) -> None:
    runners = _resolve_runners(args.runner)
    utc_time = _parse_time(args.time) if args.time else "0300"  # default: 10pm EST

    for alias, vm_name in runners.items():
        result = subprocess.run(
            [
                "az",
                "vm",
                "auto-shutdown",
                "--resource-group",
                config.RESOURCE_GROUP,
                "--name",
                vm_name,
                "--time",
                utc_time,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"  {alias}: shutdown extended to {utc_time} UTC")
        else:
            print(f"  {alias}: failed - {result.stderr.strip()}")


def cmd_setup(args: argparse.Namespace) -> None:
    """One-time provisioning: create resource group and deploy Bicep."""
    from pathlib import Path

    bicep_path = Path(__file__).parent.parent / "main.bicep"
    if not bicep_path.exists():
        print(f"Bicep file not found: {bicep_path}")
        sys.exit(1)

    ssh_key = args.ssh_key
    if not ssh_key:
        for key_file in ["id_ed25519.pub", "id_rsa.pub"]:
            p = Path.home() / ".ssh" / key_file
            if p.exists():
                ssh_key = p.read_text().strip()
                break
        if not ssh_key:
            print("No SSH public key found. Pass --ssh-key or have ~/.ssh/id_ed25519.pub")
            sys.exit(1)

    print(f"Creating resource group {config.RESOURCE_GROUP}...")
    subprocess.run(
        [
            "az",
            "group",
            "create",
            "--name",
            config.RESOURCE_GROUP,
            "--location",
            config.LOCATION,
            "--output",
            "none",
        ],
        check=True,
    )

    import secrets

    win_password = f"P@{secrets.token_urlsafe(16)}"

    result = subprocess.run(
        ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    user_object_id = result.stdout.strip()

    print("Deploying Bicep template...")
    subprocess.run(
        [
            "az",
            "deployment",
            "group",
            "create",
            "--resource-group",
            config.RESOURCE_GROUP,
            "--template-file",
            str(bicep_path),
            "--parameters",
            f"sshPublicKey={ssh_key}",
            f"windowsAdminPassword={win_password}",
            f"kvAdminObjectId={user_object_id}",
            "--output",
            "none",
        ],
        check=True,
    )

    print("\nWindows admin password stored in Key Vault (kv-quicksand-runners).")
    print()
    print("VMs provisioned. Next: register the GitHub runner agent on each.")
    print("Go to https://github.com/microsoft/quicksand/settings/actions/runners/new")
    print("and follow the instructions for each platform.")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="runners",
        description="Manage self-hosted GitHub Actions runner VMs on Azure",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn in [("start", cmd_start), ("stop", cmd_stop), ("status", cmd_status)]:
        p = sub.add_parser(name)
        p.add_argument(
            "runner",
            nargs="?",
            choices=list(config.RUNNERS),
            help="Runner to target (default: all)",
        )
        p.set_defaults(func=fn)

    p_extend = sub.add_parser("extend", help="Extend auto-shutdown time")
    p_extend.add_argument(
        "time",
        nargs="?",
        help="New shutdown time, e.g. '10pm', '11pm' (default: 10pm EST)",
    )
    p_extend.add_argument(
        "runner",
        nargs="?",
        choices=list(config.RUNNERS),
        help="Runner to target (default: all)",
    )
    p_extend.set_defaults(func=cmd_extend)

    p_setup = sub.add_parser("setup", help="One-time VM provisioning")
    p_setup.add_argument("--ssh-key", help="SSH public key string")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
