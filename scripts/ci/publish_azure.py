#!/usr/bin/env python3
"""Publish wheels to Azure Artifacts using managed identity.

Acquires a bearer token via ManagedIdentityCredential (Azure IMDS) and
uploads changed wheels in the dist directory using twine.

Usage (CI):
    UVR_PLAN='...' python scripts/ci/publish_azure.py
    UVR_PLAN='...' python scripts/ci/publish_azure.py --dist dist/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from glob import glob
from pathlib import Path

# Azure DevOps resource scope (for token acquisition)
ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"
REPO_URL = "https://pkgs.dev.azure.com/msraif/_packaging/packages/pypi/upload/"


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish wheels to Azure Artifacts")
    parser.add_argument(
        "--dist", default="dist/", help="Directory containing wheel files (default: dist/)"
    )
    args = parser.parse_args()

    plan_json = os.environ.get("UVR_PLAN")
    if not plan_json:
        print("ERROR: UVR_PLAN environment variable not set", file=sys.stderr)
        return 1

    plan = json.loads(plan_json)
    changed = set(plan.get("changed", {}))
    if not changed:
        print("No changed packages in plan — nothing to publish.")
        return 0

    # Convert package names to dist name prefixes (e.g. quicksand-core -> quicksand_core)
    dist_prefixes = {name.replace("-", "_") for name in changed}

    all_wheels = sorted(glob(f"{args.dist}/*.whl"))
    wheels = [w for w in all_wheels if any(Path(w).name.startswith(p + "-") for p in dist_prefixes)]

    if not wheels:
        print(
            f"ERROR: no .whl files found for changed packages: {', '.join(sorted(changed))}",
            file=sys.stderr,
        )
        return 1
    print(f"Publishing {len(wheels)} wheels for {len(changed)} changed packages...")

    from azure.identity import ManagedIdentityCredential

    token = ManagedIdentityCredential().get_token(ADO_SCOPE).token

    # Upload one at a time so a 409 (already exists) doesn't abort the batch.
    failed = []
    for wheel in wheels:
        name = Path(wheel).name
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "twine",
                "upload",
                "--repository-url",
                REPO_URL,
                "--username",
                "VssSessionToken",
                "--password",
                token,
                wheel,
            ],
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            print(f"  {name}: uploaded", flush=True)
        elif "409" in output or "Conflict" in output or "already contains" in output:
            print(f"  {name}: already exists, skipping", flush=True)
        else:
            print(f"  {name}: FAILED", flush=True)
            print(output, flush=True)
            failed.append(name)

    if failed:
        print(f"ERROR: {len(failed)} wheel(s) failed to upload", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
