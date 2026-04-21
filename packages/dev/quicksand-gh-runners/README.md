# quicksand-gh-runners

CLI to manage persistent self-hosted GitHub Actions runner VMs on Azure.

## Prerequisites

Set these environment variables:

```bash
export AZURE_SUBSCRIPTION_ID="<your-subscription-id>"
export ADO_ORG="<your-ado-org>"        # only needed for feed-access
export ADO_FEED="<your-ado-feed>"      # only needed for feed-access
```

## Setup

One-time provisioning creates the Azure resource group, VNet, Key Vault, and three runner VMs (Linux x64, Linux arm64, Windows x64) via a Bicep template:

```bash
quicksand-runners setup
```

This deploys Ubuntu 24.04 LTS VMs (Linux) and Windows Server 2022 (Windows), each with 256 GB Premium SSD and auto-shutdown at 9 pm EST. Linux VMs are bootstrapped with cloud-init to install Docker, Buildx, uv, and KVM support.

After provisioning, register the GitHub runner agent on each VM by following the instructions at the repo's runner settings page.

## Usage

```bash
quicksand-runners start [runner]     # Start runner VMs
quicksand-runners stop [runner]      # Deallocate runner VMs
quicksand-runners status [runner]    # Show power state and shutdown time
quicksand-runners extend [time]      # Extend auto-shutdown (e.g. "11pm")
quicksand-runners feed-access        # Grant runner managed identity access to Azure Artifacts
```

The `runner` argument is optional -- omit it to target all runners. Valid values: `x64`, `arm64`, `win`.

## Architecture

| VM | Azure Size | OS | Labels |
|----|-----------|-----|--------|
| `quicksand-runner-x64` | Standard_D4s_v5 | Ubuntu 24.04 | `self-hosted, linux, x64` |
| `quicksand-runner-arm64` | Standard_D4ps_v5 | Ubuntu 24.04 (arm64) | `self-hosted, linux, arm64` |
| `quicksand-runner-win` | Standard_D4s_v5 | Windows Server 2022 | `self-hosted, windows, x64` |

All VMs are in a single VNet with inbound traffic denied by NSG. The x64 Linux runner has a system-assigned managed identity for publishing to Azure Artifacts.

## Configuration

Runner names, Azure resource group, and location are in `quicksand_gh_runners/config.py`. Sensitive values (subscription ID, ADO org/feed) are read from environment variables.
