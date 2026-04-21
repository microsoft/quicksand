# `uvr workflow validate`

Check an existing `release.yml` against the expected schema.

```bash
uvr workflow validate
```

Reports whether the workflow is valid, invalid, or has warnings.

- **Errors**: invalid top-level keys or missing required fields.
- **Warnings**: modifications to frozen fields on core jobs (`if`, `strategy`, `runs-on`, `steps` on build/release/bump).
- **Custom jobs** are accepted without validation — the workflow model allows extra jobs.

## Flags

| Flag | Description |
|------|-------------|
| `--workflow-dir DIR` | Workflow directory (default: `.github/workflows`) |
