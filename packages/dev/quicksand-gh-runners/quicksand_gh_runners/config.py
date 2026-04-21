"""Azure configuration for runner VMs."""

SUBSCRIPTION_ID = "d4fe558f-6660-4fe7-99ec-ae4716b5e03f"  # MSR LIT
RESOURCE_GROUP = "rg-quicksand-runners"
LOCATION = "eastus"

RUNNERS = {
    "x64": "quicksand-runner-x64",
    "arm64": "quicksand-runner-arm64",
    "win": "quicksand-runner-win",
}

DEFAULT_SHUTDOWN_TIME = "0200"  # 9pm EST = 02:00 UTC
SHUTDOWN_TIMEZONE = "UTC"

# Azure DevOps
ADO_ORG = "msraif"
ADO_FEED = "packages"
ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"
