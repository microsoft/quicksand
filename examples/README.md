# Quicksand Examples

Each example is a standalone script that can be run directly.

## Installation

First install quicksand:

```bash
pip install quick-sandbox
```

**For most examples (Ubuntu):**
```bash
quicksand install ubuntu
```

**For Alpine examples:**
```bash
quicksand install alpine
```

**For custom image examples:**
```bash
quicksand install dev
```

**For desktop examples:**
```bash
quicksand install ubuntu-desktop
```

## Examples

| File | Description | Requires |
|------|-------------|----------|
| `simple_usage.py` | Basic command execution | ubuntu |
| `custom_config.py` | Memory, CPUs, mounts, ports, network | ubuntu |
| `accelerator_config.py` | Hardware acceleration options | ubuntu |
| `mounts.py` | Boot-time and hot mounts | ubuntu |
| `checkpoint_revert.py` | In-session snapshots with revert | ubuntu |
| `save.py` | Save and reload sandbox state | ubuntu |
| `save_load_large.py` | Save/load stress test with large packages | ubuntu or alpine |
| `error_handling.py` | Exit codes, stderr, timeouts | ubuntu |
| `custom_image.py` | Use a custom image from Dockerfile | ubuntu + dev |
| `gui_input.py` | Desktop GUI automation (keyboard, mouse) | ubuntu-desktop |

## Running

```bash
# Run any example directly
python examples/simple_usage.py

# Or from the examples directory
cd examples
python simple_usage.py
```
