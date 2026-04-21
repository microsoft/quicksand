# `uvr workflow init`

Scaffold the GitHub Actions release workflow into your repo.

```bash
uvr workflow init
```

Creates `.github/workflows/release.yml` with three core jobs: **build**, **release**, and **bump**. Run this once when setting up a new repo.

The generated workflow contains only the core pipeline. You can add custom jobs (tests, linting, PyPI publish, etc.) by editing the YAML directly — see `pipeline.md`.

## Flags

| Flag | Description |
|------|-------------|
| `--force` | Overwrite an existing `release.yml` (loses any custom jobs you added) |
| `--workflow-dir DIR` | Write to a different directory (default: `.github/workflows`) |

## Upgrading

When a new version of `uvr` ships template changes, run:

```bash
uvr workflow init --upgrade
```

This performs a three-way merge between your current workflow, the old template (stored in `.uvr/bases/`), and the new template. Custom jobs you added are preserved. If conflicts arise, your `--editor` is opened.

If merge bases are missing (e.g. you upgraded from an older `uvr` that didn't track them), `uvr` will prompt you. To recover bases without modifying files:

```bash
uvx --from uv-release-monorepo==<old-version> uvr workflow init --base-only
uvr workflow init --upgrade
```

## Notes

- Requires a git repo with a `pyproject.toml` that defines `[tool.uv.workspace]` members.
- After scaffolding, edit the workflow to add custom jobs for your project's quality gates.
- Run `uvr workflow validate` after manual edits to catch schema errors (see `cmd-validate.md`).
- `--force` overwrites the entire file. If you have custom jobs, back up first or re-add them after.
