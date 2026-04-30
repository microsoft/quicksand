# Quicksand Image Tools

This package provides development tools for building custom VM images for the [quicksand](https://github.com/microsoft/quicksand) agent harness.

## Installation

```bash
pip install quick-sandbox
quicksand install dev
```

## CLI Usage

The recommended way to build custom images:

```bash
# Initialize a build directory (creates Dockerfile, builds base image if needed)
quicksand-image-tools init ./my-image ubuntu    # or: alpine

# Customize the generated Dockerfile, then build the VM image
quicksand-image-tools build-image ./my-image/Dockerfile -o my-image.qcow2
```

### Example Dockerfile

After running `quicksand-image-tools init ./my-image ubuntu`, a Dockerfile is created with the versioned base image. Add your customizations:

```dockerfile
FROM quicksand/ubuntu-base:24.04.0

# Add your customizations here
RUN apt-get update && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*
```

The base images include the kernel, agent, and init configuration. You just add your packages.

## Python API

Build a VM image programmatically:

```python
import asyncio
from quicksand_image_tools import build_image
from quicksand import Sandbox, SandboxConfig

async def main():
    # Build from a Dockerfile path
    image_path = build_image("./Dockerfile")

    # Use the custom image
    async with Sandbox(SandboxConfig(image_path=str(image_path))) as sb:
        result = await sb.execute("node --version")
        print(result.stdout)

asyncio.run(main())
```

## Dockerfile Requirements

When building FROM a base image (`quicksand/ubuntu-base` or `quicksand/alpine-base`), you just add your packages - the base handles everything else.

For scratch Dockerfiles, you must:

1. **Install a kernel**: Ubuntu `linux-image-virtual` or Alpine `linux-virt`
2. **Build the Rust agent** via multi-stage Docker build
3. **Configure a systemd service or OpenRC script** to run the agent on boot

See the `examples/custom_image.py` for a complete working example.

## CLI Reference

### `quicksand-image-tools init [directory] [ubuntu|alpine]`

Initialize a directory for building custom images.

- If no Dockerfile exists in the directory, you must specify a base image type (`ubuntu` or `alpine`)
- Automatically builds the base Docker image if it doesn't exist
- Creates a Dockerfile with the appropriate `FROM` statement

### `quicksand-image-tools build-base [ubuntu|alpine|all]`

Build base Docker images locally. Creates versioned images (e.g., `quicksand/ubuntu-base:24.04.0`) and tags them as `latest`. The version matches the installed `quicksand-ubuntu` or `quicksand-alpine` package version.

### `quicksand-image-tools build-image <dockerfile> [-o output] [--cache-dir dir]`

Build a VM image from a Dockerfile.

## Python API Reference

### `build_image(dockerfile, output_path=None, cache_dir=None)`

Build a VM image from a Dockerfile.

- `dockerfile`: Path to a Dockerfile
- `output_path`: Where to save the qcow2 image (default: cached by hash)
- `cache_dir`: Cache directory (default: `~/.cache/quicksand/images/`)

Returns the `Path` to the built qcow2 image.

### `get_agent_source_dir()`

Returns the `Path` to the Rust agent source directory (`quicksand-guest-agent/`). This is used by image packages to set up symlinks for Docker builds.

## How It Works

1. Builds a Docker image from your Dockerfile
2. Creates a container and exports its filesystem
3. Extracts kernel and initrd from `/boot`
4. Converts the filesystem to a bootable qcow2 VM image

## Requirements

- Docker must be installed and running
- QEMU tools (bundled with quicksand-core, or install separately)

## License

MIT
