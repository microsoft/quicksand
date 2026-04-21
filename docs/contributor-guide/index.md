# Contributor Guide

See also: [User Guide](../user-guide/) (Python API) and [Under the Hood](../under-the-hood/) (QEMU flags).

## Requirements

- **Python 3.11+** and **uv** for the workspace and all packages
- **Docker** is required for building base images (Dockerfiles are compiled into qcow2 VM images)
- **QEMU** is required for integration tests and building overlay images (overlays boot a real VM). Install the bundled version with `quicksand install qemu`, or use a system QEMU on `PATH`.

Lint, type checking, and unit tests (`poe ci`) work without Docker or QEMU.

## Dev setup

```bash
git clone https://github.com/microsoft/quicksand
cd quicksand
uv sync --all-packages --all-groups --all-extras
quicksand install qemu ubuntu    # needed for integration tests
uv run poe ci                       # lint + typecheck + tests
```

Before committing:
```bash
uv run poe fix && uv run poe check
```

## Common tasks

| Command | What it does |
|---------|-------------|
| `uv run poe fix` | Auto-fix lint + formatting |
| `uv run poe check` | Lint + format check + type check |
| `uv run poe test` | Unit tests (no QEMU needed) |
| `uv run poe test-integration` | Integration tests (needs QEMU + images) |
| `uv run poe test-smb` | SMB server unit tests |
| `uv run poe ci` | Full CI: check + test |

