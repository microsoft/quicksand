---
name: release
description: Release packages to GitHub via uvr. Use when user says "release", "publish packages", "cut a release", or wants to publish new package versions.
disable-model-invocation: true
---

# Releasing Packages

Prerequisites: `uvr` (`uv tool install uv-release-monorepo`) and `gh`.

For first-time setup, scaffold the workflow with `uvr workflow init` (see `references/cmd-init.md`). To install the Claude skills into your project, see `references/cmd-skill-init.md`.

If the project has existing CI checks (tests, linting, etc.) that aren't yet wired into the release workflow, see `references/custom-jobs.md` before your first release.

## 0. Self-hosted runner

The arm64 macOS builds require a self-hosted runner. Before dispatching, start the runner agent in a background shell:

```bash
gha-runners/local/run.sh &
```

Confirm it registers with GitHub before proceeding.

## 1. Branch

You must not be on main. If you are, create a release branch and switch to it.

The working tree must be clean. Run `git status`. If dirty, ask the user whether to stash, commit, or abort.

## 2. Preview Changes

```bash
uvr release
```

This prints the plan and prompts `Dispatch release? [y/N]`. Decline (`N`) to preview without dispatching. See `references/cmd-release.md` for all flags.

Present the output to the user. For each changed package, show:
- The package name and its new version
- Why it changed (summarize the relevant commits)

Ask the user whether any packages need a minor bump instead of patch. Patch is the default — bump minor for new features, new public API, or breaking changes:

```bash
uv version --bump minor --directory packages/<package-name>
```

## 3. Review

For each changed package, verify its public API against its docs:

1. Read the current public API
2. Check each item on this list:
   - Do the package's docstrings accurately reflect the exported types and internal functionality? Audit natural language descriptions, argument lists, returns notes, code examples, and references.
   - Do all docs (READMEs, docs/, etc) accurately reflect the public API and internal functionality of the package? Audit natural language descriptions, code examples, and references.
3. Fix any discrepancies before continuing

## 4. CHANGELOG.md and Release Notes

Update CHANGELOG.md on the release branch before committing. Format:

For each package, review the commits since the baseline. Use the **DIFF FROM** column from the `uvr release --dry-run` output — this is the actual tag the planner diffs against, which may differ from the PREVIOUS version (e.g. a `-base` tag for dev cycles vs a release tag):

```bash
git log --oneline <DIFF_FROM_TAG>..HEAD -- packages/<pkg>
```

Example entry:

```markdown
## [v0.3.4] - 2026-03-18

### Fixed
- CIFS directory listing filename truncation
```

Section headers: **Added**, **Changed**, **Deprecated**, **Removed**, **Fixed**, **Security**.

For each changed package, also write release notes to `.uvr/release-notes/<pkg>/<version>.md`. This directory is gitignored — notes are ephemeral and consumed at release time.

Review the commits since the last release:

```bash
git log --oneline <pkg>/v<last-version>..HEAD -- packages/<pkg>
```

Draft user-facing release notes — don't dump commit messages, write prose that helps users understand what's new, changed, or fixed. **Present the draft to the user for approval before writing the file.**

```bash
mkdir -p .uvr/release-notes/my-lib
cat > .uvr/release-notes/my-lib/1.2.0.md << 'EOF'
Dashboard support and stability improvements.

### Added
- New `Widget` class for building dashboards

### Fixed
- Parser no longer crashes on empty input
EOF
```

If no `--release-notes` flag is provided, the release gets a minimal header only.

## 5. Dispatch

```bash
git add -A
git commit -m "Release v<VERSION>"
git push -u origin "$(git branch --show-current)"
uvr release
```

Or with custom release notes:

```bash
uvr release \
  --release-notes my-lib @.uvr/release-notes/my-lib/1.2.0.md
```

When prompted `Dispatch release? [y/N]`, answer `y`.

If `uvr release` says dependency pins were updated, commit those first and re-run:

```bash
git add -A
git commit -m "chore: update dep pins"
git push
uvr release
```

## 6. Monitor

```bash
gh run list --workflow=release.yml --limit=1
gh run watch <RUN_ID> --exit-status
```

If the workflow fails, check which job failed:

```bash
gh run view <RUN_ID> --log-failed
```

If the failure is early (build broke), fix the issue and re-dispatch from scratch:

```bash
git add <files>
git commit -m "Fix: <description>"
git push
uvr release
```

If a later job failed but earlier jobs succeeded, use `--skip-to` and `--reuse-*` flags to resume without re-running what already passed. See `references/troubleshooting.md#resuming-a-partially-failed-release` for the full decision tree.

## 7. Verify

```bash
gh release list --limit 15       # confirm per-package releases exist
```

If something goes wrong, see `references/troubleshooting.md`.

## 8. Merge

**ALWAYS** merge stable release branches back to main:

```bash
git checkout main
git pull --rebase
git merge --no-ff <release-branch> -m "Merge <release-branch>"
git push
```

After merging, clean up release notes:

```bash
rm -rf .uvr/release-notes/
```

**DO NOT** merge pre-release branches back to main. Stay on the branch through the alpha → beta → rc → stable cycle, then merge after the stable release. See `references/pre-releases.md`.

**TAKE CARE** merging post-release branches back to main — they branch from an old tag, so pyproject.toml versions will conflict. You may need to accept main's versions or cherry-pick just the fix commits. See `references/post-releases.md`.

---

## Example

User says: "Let's release the new changes"

1. Verify not on main, create a release branch
2. Run `uvr release`, decline the prompt — shows `my-lib` is dirty (2 commits: added export, fixed parser)
3. Present to user: "my-lib will bump 0.2.1 -> 0.2.2 (patch). It has a new public export — should this be a minor bump instead?"
4. User says "yes, bump minor" — run `uvr bump --package my-lib --minor`
5. Review docstrings and docs against current API — new `Parser` class exported but not documented. Fix docs.
6. Draft release notes: "Added `Parser` class for structured input handling. Fixed crash on empty input." Present to user for approval.
7. Write approved notes to `.uvr/release-notes/my-lib/0.3.0.md`
8. Commit, push, run `uvr release --release-notes my-lib @.uvr/release-notes/my-lib/0.3.0.md` and confirm
9. Monitor workflow, verify GitHub releases
10. Merge release branch back to main, clean up `.uvr/release-notes/`

## References

**Commands:**
- `references/cmd-init.md` — scaffold the release workflow
- `references/cmd-validate.md` — check release.yml against schema
- `references/cmd-release.md` — plan and dispatch a release (all flags)
- `references/cmd-runners.md` — manage per-package build runners
- `references/cmd-install.md` — install from GitHub releases
- `references/cmd-skill-init.md` — copy Claude skills into project

**Guides:**
- `references/pipeline.md` — the three core jobs (build, publish, bump)
- `references/release-plan.md` — what the release plan JSON contains
- `references/custom-jobs.md` — how to add your own jobs to the workflow
- `references/dev-releases.md` — publishing `.devN` versions for testing
- `references/pre-releases.md` — alpha, beta, and release candidate versions
- `references/post-releases.md` — correcting an already-released version
- `references/troubleshooting.md` — common problems and fixes
