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


def _get_compute_client() -> ComputeManagementClient:
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
    """Get the auto-shutdown time for a VM from DevTestLab schedules."""
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


def _parse_time(time_str: str) -> str:
    """Parse a human time like '10pm', '11pm', '22:00' into HHMM UTC."""
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


def cmd_feed_access(_args: argparse.Namespace) -> None:
    """Register the x64 runner's managed identity in Azure DevOps and grant feed access."""
    import requests

    # 1. Get the managed identity principal ID from the x64 runner VM
    client = _get_compute_client()
    vm = client.virtual_machines.get(config.RESOURCE_GROUP, config.RUNNERS["x64"])
    if not vm.identity or not vm.identity.principal_id:
        print("ERROR: quicksand-runner-x64 has no system-assigned managed identity")
        sys.exit(1)
    principal_id = vm.identity.principal_id
    print(f"Managed identity principal ID: {principal_id}")

    # 2. Get a bearer token for Azure DevOps
    credential = DefaultAzureCredential()
    token = credential.get_token(config.ADO_SCOPE).token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 3. Register as a service principal in the Azure DevOps org
    print(f"Registering service principal in {config.ADO_ORG}...")
    resp = requests.post(
        f"https://vssps.dev.azure.com/{config.ADO_ORG}/_apis/graph/serviceprincipals?api-version=7.1-preview.1",
        headers=headers,
        json={"originId": principal_id},
    )
    if resp.status_code == 409:
        print("  Already registered — fetching existing descriptor...")
        # List service principals and find the one matching our principal ID
        list_resp = requests.get(
            f"https://vssps.dev.azure.com/{config.ADO_ORG}/_apis/graph/serviceprincipals?api-version=7.1-preview.1",
            headers=headers,
        )
        list_resp.raise_for_status()
        sps = list_resp.json().get("value", [])
        descriptor = next(
            (sp["descriptor"] for sp in sps if sp.get("originId") == principal_id), None
        )
        if not descriptor:
            print("ERROR: could not find service principal descriptor")
            sys.exit(1)
    else:
        resp.raise_for_status()
        descriptor = resp.json()["descriptor"]
    print(f"  Subject descriptor: {descriptor}")

    # 4. Resolve subject descriptor to an identity descriptor (needed by feed permissions API)
    print("  Resolving identity descriptor...")
    resp = requests.get(
        f"https://vssps.dev.azure.com/{config.ADO_ORG}/_apis/identities?subjectDescriptors={descriptor}&api-version=7.1-preview.1",
        headers=headers,
    )
    resp.raise_for_status()
    identities = resp.json().get("value", [])
    if not identities:
        print("ERROR: could not resolve identity descriptor", file=sys.stderr)
        sys.exit(1)
    identity_descriptor = identities[0]["descriptor"]
    print(f"  Identity descriptor: {identity_descriptor}")

    # 5. Grant contributor access to the artifacts feed
    # Role IDs: 1=reader, 2=collaborator, 3=contributor, 4=administrator
    print(f"Granting contributor access to feed '{config.ADO_FEED}'...")
    resp = requests.patch(
        f"https://feeds.dev.azure.com/{config.ADO_ORG}/_apis/packaging/feeds/{config.ADO_FEED}/permissions?api-version=7.1-preview.1",
        headers=headers,
        json=[{"identityDescriptor": identity_descriptor, "role": 3}],
    )
    if not resp.ok:
        print(f"  ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    print("  Done.")


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
        prog="quicksand-runners",
        description="Manage self-hosted GitHub Actions runner VMs",
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
        "time", nargs="?", help="New shutdown time, e.g. '10pm', '11pm' (default: 10pm EST)"
    )
    p_extend.add_argument(
        "runner", nargs="?", choices=list(config.RUNNERS), help="Runner to target (default: all)"
    )
    p_extend.set_defaults(func=cmd_extend)

    p_setup = sub.add_parser("setup", help="One-time VM provisioning")
    p_setup.add_argument("--ssh-key", help="SSH public key string")
    p_setup.set_defaults(func=cmd_setup)

    p_feed = sub.add_parser(
        "feed-access", help="Grant runner managed identity access to Azure Artifacts feed"
    )
    p_feed.set_defaults(func=cmd_feed_access)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
