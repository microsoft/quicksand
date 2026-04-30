---
name: new-overlay-image
description: >
  How to create overlay image packages that layer on top of base images.
  Use when creating a new overlay package like quicksand-agent, scaffolding
  an overlay, or modifying hatch_build.py for overlay builds.
---

# Creating Overlay Image Packages

Overlay packages (like `quicksand-agent`) boot a base VM, install software, and save the result as an overlay. They're faster to create than base images and layer on top of existing base images.

## Quick Start

Use the scaffold tool:

```bash
quicksand dev scaffold overlay my-overlay-package --base ubuntu
```

This creates `my-overlay-package/` from the overlay template and renames everything. Then edit the `_setup()` function in `hatch_build.py`.

## Package Structure

```
{name}/
├── pyproject.toml
├── hatch_build.py              # Boots VM, runs setup, saves overlay
├── README.md
└── {module_name}/
    ├── __init__.py             # ImageProvider entry point
    ├── sandbox.py              # Optional: custom Sandbox subclass
    └── images/                 # Built overlay save (git-ignored)
        ├── manifest.json
        └── overlays/*.qcow2
```

## How It Works

During `uv build`, the `hatch_build.py` hook:
1. Boots a base VM (default: Ubuntu) with full network access
2. Runs `_setup(shell)` — your install commands
3. Saves the VM state as a compressed overlay
4. Packages the overlay into the wheel

## hatch_build.py

Reference: `packages/contrib/quicksand-agent/hatch_build.py`

The key function to edit:

```python
async def _setup(shell: Shell) -> None:
    """Install steps for your overlay."""
    await shell("apt-get update", timeout=120)
    await shell("apt-get install -y your-packages", timeout=300)
    await shell("pip install your-python-deps", timeout=300)
```

The `shell` callable runs commands inside the VM, streams output, and raises on failure. Use `timeout=` for slow commands.

The build hook also:
- Sets `pure_python = False` and platform-specific wheel tags
- Skips rebuild if `images/manifest.json` already exists
- Uses `UbuntuSandboxConfig` with 4G memory, 4 CPUs, full network, 10G disk

## Python API (__init__.py)

Reference: `packages/contrib/quicksand-agent/quicksand_agent/__init__.py`

Must export:
- `image` — `_ImageProvider` instance that resolves the bundled save via `ImageResolver()._resolve_save(IMAGES_DIR)`

## pyproject.toml

```toml
[project]
name = "{name}"
version = "0.1.0"
dependencies = [
    "quicksand-core>=0.6.0",
    "quicksand-ubuntu>=0.5.0",
]

[build-system]
requires = ["hatchling", "quicksand-core", "quicksand-ubuntu", "quicksand-qemu", "quicksand-smb"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["{module_name}"]
artifacts = [
    "{module_name}/images/manifest.json",
    "{module_name}/images/overlays/*.qcow2",
]

[tool.hatch.build.targets.wheel.hooks.custom]

[project.entry-points."quicksand.images"]
{name} = "{module_name}:image"

[tool.uv.sources]
quicksand-core = { workspace = true }
quicksand-ubuntu = { workspace = true }
```

## Release

Add the package to `_shared.py:OVERLAY_PACKAGES` so the release pipeline:
- Builds it per-architecture after base images complete
- Extracts base images from build artifacts before building
- Sets up KVM (best-effort) for VM acceleration

## Building

```bash
# First build (boots VM, runs setup, saves overlay — takes a few minutes)
uv build --package {name}

# Subsequent builds (reuses cached overlay)
uv build --package {name}
```

To force rebuild, delete the images directory:
```bash
rm -rf packages/contrib/{name}/{module_name}/images/
uv build --package {name}
```

## Testing

```bash
uv run quicksand run {name}
```

```python
from quicksand_core import Sandbox

async with Sandbox(image="{name}") as sb:
    result = await sb.execute("your-command")
    print(result.stdout)
```

## Changing the Base Image

By default overlays use Ubuntu. To use Alpine or another base:
1. Change the import in `hatch_build.py` from `quicksand_ubuntu` to your base
2. Update `pyproject.toml` dependencies and build-system requires
3. Update `_setup()` commands for the new distro (e.g., `apk add` instead of `apt-get install`)
