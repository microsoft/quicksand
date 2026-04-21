# `uvr release`

Plan and execute a release. By default, generates a release plan and dispatches it to GitHub Actions via `gh workflow run`.

The release plan is a self-contained JSON document passed as the `plan` input to the workflow. It contains version numbers, build commands, skip lists, matrix entries, and everything CI needs to execute the release without reading git history. See `release-plan.md` for the plan schema.

## Basic usage

```bash
uvr release              # plan + dispatch to CI
uvr release --dry-run    # preview only
```

## Mode flags

| Flag | Description |
|------|-------------|
| `--where {ci,local}` | `ci` dispatches to GitHub Actions (default), `local` builds in this shell |
| `--dry-run` | Print what would be released without making changes |
| `--plan JSON` | Execute a pre-computed release plan locally |

## Release type

Mutually exclusive. Controls how versions are interpreted:

| Flag | Description |
|------|-------------|
| *(none)* | Final release (default) — strips `.devN` suffix |
| `--dev` | Dev release — publishes the current `.devN` version as-is (see `dev-releases.md`) |
| `--pre {a,b,rc}` | Pre-release — alpha, beta, or release candidate (see `pre-releases.md`) |
| `--post` | Post-release (see `post-releases.md`) |

## Build options

| Flag | Description |
|------|-------------|
| `--rebuild-all` | Rebuild all packages, not just changed ones |
| `--python VER` | Python version for CI builds (default: `3.12`) |

## Dispatch options (CI mode)

| Flag | Description |
|------|-------------|
| `-y, --yes` | Skip the confirmation prompt |
| `--skip JOB` | Skip a CI job (repeatable). Core jobs: `build`, `release`, `bump`. Custom jobs can also be skipped if they check the plan's skip list in their `if` condition. |
| `--skip-to JOB` | Skip all core jobs before JOB. Choices: `release`, `bump`. `--skip-to release` skips build; `--skip-to bump` skips build + release. |
| `--reuse-run RUN_ID` | Reuse build artifacts from a prior workflow run. Requires `build` to be skipped. |
| `--reuse-release` | Assume GitHub releases already exist. Requires both `build` and `release` to be skipped. |

`--reuse-run` and `--reuse-release` are mutually exclusive.

## Local mode options

| Flag | Description |
|------|-------------|
| `--no-push` | Skip `git push` after a local release |

## Output options

| Flag | Description |
|------|-------------|
| `--json` | Print only the plan JSON to stdout and exit |
| `--workflow-dir DIR` | Workflow directory (default: `.github/workflows`) |

## Common patterns

```bash
# Preview the full plan
uvr release --dry-run

# Release and skip confirmation
uvr release -y

# Resume after a failed build — reuse artifacts from run 12345678
uvr release --skip-to release --reuse-run 12345678

# Resume after release succeeded but bump failed
uvr release --skip-to bump --reuse-release

# Skip a custom job (e.g., tests you already ran locally)
uvr release --skip checks

# Build locally instead of via CI
uvr release --where local

# Get the plan as JSON for scripting
uvr release --json
```
