# Extending the Sandbox

## Add a Sandbox method

Methods live in mixins in `quicksand_core/sandbox/`. Pick the one that fits your concern:

| File | Owns |
|------|------|
| `_execution.py` | `execute()` |
| `_checkpoints.py` | `checkpoint()`, `revert()` |
| `_saves.py` | `save()` |
| `_input.py` | `screenshot()`, `type_text()`, `press_key()`, `mouse_*()` |
| `_mounts.py` | `mount()`, `unmount()` |

**Example** of adding `read_file()` to `_execution.py`:

```python
async def read_file(self, guest_path: str) -> str:
    if not self.is_running:
        raise RuntimeError("Sandbox is not running")
    result = await self.execute(f"cat {guest_path}")
    if result.exit_code != 0:
        raise FileNotFoundError(f"Guest file not found: {guest_path}: {result.stderr}")
    return result.stdout
```

If you need new shared state, add the field to `_SandboxProtocol` (`_protocol.py`) and initialize it in `Sandbox.__init__()` (`_sandbox.py`).

Export from `quicksand_core` and the `quicksand` wrapper if it's public API.

## Add a new OS

1. Add `OS.FREEBSD` to the enum in `host/os_.py`
2. Create `FreeBSDConfig(BaseOSConfig)` and implement `detect_accelerator()`, `disk_aio`, etc.
3. Add the detection branch in `OSConfig.__new__()` and `_detect_os()`
4. If it needs unique QEMU flags, handle them in `build_qemu_command()` (`qemu/platform.py`)

## Add a new architecture

1. Add `Architecture.RISCV64` to the enum in `host/arch.py`
2. Create `RISCV64Config(BaseArchitectureConfig)` in `qemu/arch.py` and set machine type, device names, console, GPU, CPU model
3. Add the detection branch in `ArchitectureConfig.__new__()` and `_detect_architecture()`
4. Build QEMU and image packages for the new architecture

## Add a new QEMU flag

1. If user-configurable, add the kwarg to `Sandbox` (and the underlying `SandboxConfig` in `_types.py`)
2. Add the flag to `build_qemu_command()` in `qemu/platform.py` (or a `_build_*_args()` helper)
3. Document in the relevant `docs/under-the-hood/` file
4. Unit test: verify the flag appears in the generated command. Integration test: verify the feature works.
