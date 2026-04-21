# quicksand-overlay-scaffold

Scaffold a new overlay image package for quicksand.

Overlay packages boot a base VM (default: Ubuntu), run setup commands to install software, and save the result as a compressed overlay. The overlay is bundled into a wheel so users can `quicksand install` and `quicksand run` it without rebuilding.

## Usage

```bash
quicksand-overlay-scaffold my-agent-sandbox
```

This creates a `my-agent-sandbox/` directory with:

```
my-agent-sandbox/
├── pyproject.toml              # Package metadata and entry points
├── hatch_build.py              # Edit _setup() with your install steps
├── README.md
└── my_agent_sandbox/
    ├── __init__.py             # ImageProvider entry point
    ├── sandbox.py              # Optional Sandbox subclass
    └── images/                 # Built overlay (populated during uv build)
```

## Next Steps

1. Edit `hatch_build.py` — add your install commands to `_setup()`:

```python
async def _setup(shell: Shell) -> None:
    await shell("apt-get update", timeout=120)
    await shell("apt-get install -y your-packages", timeout=300)
    await shell("pip install your-python-deps", timeout=300)
```

2. Build the package (boots a VM, runs setup, saves overlay):

```bash
uv build --package my-agent-sandbox
```

3. Test it:

```bash
quicksand run my-agent-sandbox
```

## How It Works

During `uv build`, the `hatch_build.py` hook:

1. Boots an Ubuntu VM with 4G memory, 4 CPUs, full network, 10G disk
2. Runs your `_setup(shell)` function inside the VM
3. Saves the VM state as a compressed qcow2 overlay
4. Packages the overlay into a platform-specific wheel

Subsequent builds skip the VM step if `images/manifest.json` already exists. Delete the `images/` directory to force a rebuild.