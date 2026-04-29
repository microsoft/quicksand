# Testing

## Running tests

```bash
uv run poe test                  # Unit tests (no QEMU needed)
uv run poe test:integration      # Integration tests (needs QEMU + images)
uv run poe ci                    # Full CI: check + test

# Specific file or test
uv run pytest tests/unit/test_core_config.py
uv run pytest tests/unit/test_core_config.py::test_default_config -v
```

## Writing a unit test

Unit tests go in `tests/unit/`. They don't need QEMU. Mock dependencies as needed.

```python
def test_my_feature():
    from quicksand_core import SandboxConfig
    config = SandboxConfig(image="ubuntu", memory="1G")  # SandboxConfig for internal/unit testing
    assert config.memory == "1G"
```

## Writing an integration test

Integration tests go in `tests/integration/`. They boot real VMs.

```python
import pytest

@pytest.mark.integration
async def test_my_feature(tmp_dir):
    async with Sandbox(image="ubuntu") as sb:
        result = await sb.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout
```

## Markers

| Marker | Use when |
|--------|----------|
| `@pytest.mark.integration` | Test needs QEMU — skipped by `poe test` |
| `@pytest.mark.slow` | Test takes significant time |
| `@pytest.mark.docker` | Test needs Docker daemon |

Skip conditions (`skip_no_qemu`, `skip_no_docker`, `skip_no_mke2fs`) are in `tests/conftest.py`.
