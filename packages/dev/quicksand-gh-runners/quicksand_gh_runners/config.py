"""Azure configuration for runner VMs."""

import os

SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = "rg-quicksand-runners"
LOCATION = "eastus"

RUNNERS = {
    "x64": "quicksand-runner-x64",
    "arm64": "quicksand-runner-arm64",
    "win": "quicksand-runner-win",
}

DEFAULT_SHUTDOWN_TIME = "0200"  # 9pm EST = 02:00 UTC
SHUTDOWN_TIMEZONE = "UTC"
