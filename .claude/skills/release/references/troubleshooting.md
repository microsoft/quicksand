# Troubleshooting

## Pin updates block the release

If `uvr release` says pins were updated, it exits without dispatching. Commit the pin changes and re-run:

```bash
git add -A
git commit -m "chore: update dep pins"
git push
uvr release
```

## Partial release failure (some packages published, some failed)

1. Check `gh run view <RUN_ID> --log-failed` to identify which packages failed
2. Fix the root cause (usually a build error in one package)
3. Re-run `uvr release` — it will only re-publish packages that haven't been released yet

## Tags pushed but workflow didn't trigger

Verify the workflow trigger in `.github/workflows/release.yml` matches expectations. Manually dispatch if needed:

```bash
gh workflow run release.yml
```

## Custom job failed (e.g., PyPI publish)

Check the job logs:

```bash
gh run list --workflow=release.yml --limit=5
gh run view <RUN_ID> --log-failed
```

## Resuming a partially failed release

When a release fails partway through, you don't need to start over. Use `--skip`, `--skip-to`, and `--reuse-*` flags to resume from where it broke. See `cmd-release.md` for the full flag reference.

**First, identify what succeeded.** Check the failed run:

```bash
gh run view <RUN_ID> --log-failed
```

The release pipeline has three core jobs in order: **build → release → bump**. Pick the right resume strategy based on where it failed:

### Build failed

Nothing was published — just fix the issue and re-run normally:

```bash
uvr release
```

### Build succeeded, release failed

Reuse the build artifacts so you don't rebuild:

```bash
uvr release --skip-to release --reuse-run <RUN_ID>
```

`--skip-to release` skips the build job. `--reuse-run` downloads artifacts from the prior run.

### Release succeeded, bump failed

GitHub releases already exist, so tell uvr to skip both build and release:

```bash
uvr release --skip-to bump --reuse-release
```

`--reuse-release` means "don't try to create releases that already exist."

### Skipping custom jobs

If your workflow has custom jobs (tests, linting, etc.) and you want to skip one — for example, because you already ran checks locally:

```bash
uvr release --skip checks
```

`--skip` is repeatable. To skip multiple jobs:

```bash
uvr release --skip checks --skip docs
```

Custom jobs must check the plan's skip list in their `if` condition for this to work. See `custom-jobs.md`.

### Constraints

- `--reuse-run` requires build to be skipped (via `--skip build` or `--skip-to release`/`--skip-to bump`)
- `--reuse-release` requires both build and release to be skipped (via `--skip-to bump`)
- `--reuse-run` and `--reuse-release` are mutually exclusive

## Main moved ahead of the release branch

If other work was merged to main between when you branched and when the release completed, you'll see conflicts when merging back.

**What happened:** The bump job bumps versions and pins deps on the release branch. Meanwhile, main may have new commits that touch pyproject.toml, uv.lock, or the same source files.

**How to resolve:**

```bash
git checkout main
git pull --rebase
git merge --no-ff <release-branch>
# resolve conflicts:
#   - pyproject.toml versions: accept the release branch's versions (they have the post-release .dev bumps)
#   - uv.lock: regenerate with `uv sync` after resolving pyproject.toml
#   - source conflicts: merge normally
git add -A
uv sync
git add uv.lock
git commit
git push
```

After merging, verify the version bumps from the bump job landed cleanly. Check pyproject.toml versions match what the bump job set.

**If the merge is too messy**, an alternative is to skip the merge and cherry-pick only your pre-release commits onto main, then let the next release pick up the changes naturally.

## Rolling back a bad release

GitHub releases can be deleted, but published packages (e.g., on PyPI) generally cannot. If a broken version was published:

1. Delete the GitHub release: `gh release delete <tag> --yes`
2. Delete the tag: `git push --delete origin <tag>`
3. Fix the issue, bump the version again, and re-release
