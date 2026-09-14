# Releasing

Releases are managed by `uvr`, with the repository's `/release` skill as the workflow guide.

## Flow

1. **Prepare.** Use a non-main branch based on main. Run `uv run uvr status` to inspect changed packages and their baseline tags.
2. **Version.** Select feature/minor versions and dependency updates with `uv run uvr version`. Strip development suffixes from release targets with `--bump stable`, update the changelog, then commit and push.
3. **Preview.** Run `uv run uvr release --dry-run`. The worktree must be clean and local HEAD must match the remote branch.
4. **Dispatch.** Run `uv run uvr release`, supplying approved notes with `--release-notes PACKAGE @FILE`. The CLI generates the required workflow `plan` input.
5. **Verify and merge.** Monitor every build, license, release, PyPI publish, and next-development-version job. Verify published artifacts and installation, then fetch and merge the remote release branch tip back to main, including the CI-generated development-version commit.

The release workflow runs unit checks, but its VM integration job is currently
disabled. Run relevant VM checks separately before publishing.

## Versioning

| Bump | When |
|------|------|
| **Patch** (automatic) | Bug fixes, CI fixes, doc updates, refactors |
| **Minor** (manual) | New features, new public API, breaking changes, new packages |

Patch versions are set automatically by the previous release's dev bump.

## Per-package releases

Each package gets its own GitHub release (`quicksand-core/v0.13.0`, `quicksand-qemu/v0.5.12`, etc.). Unchanged packages keep their existing releases. `uvr` detects committed file changes; version commands update internal dependency ranges when required.

## Runners and package indexes

Runner assignments live in `[tool.uvr.runners]` in `pyproject.toml`. The Windows
ARM64 QEMU build uses GitHub's `windows-11-arm` runner. Linux and Windows x64
builds use the configured Azure VMs, and ARM64 image builds use the local macOS
runner. Set `AZURE_SUBSCRIPTION_ID` to the intended subscription before running
`uv run quicksand-runners start`; this command manages Azure VMs, not the macOS
runner.

Build jobs use uv 0.12.13 and install `quick-sandbox`, its extras, and the explicit
`dev` group containing the release CLI. Azure runner-management dependencies are
not needed to build wheels and are intentionally excluded from this bootstrap
environment.

Each build job uses an isolated `UV_CACHE_DIR` for that run and attempt.
uv treats filenames in `--find-links` directories as immutable, so a persistent
cache can silently install an earlier wheel when an unreleased version is rebuilt.
Disabling the setup action's cache does not disable a self-hosted runner's local
uv cache. Docker's build cache remains available across attempts.

Agent and CUA overlay Python installs inherit the host's `PIP_INDEX_URL`,
`PIP_EXTRA_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_INDEX_URL`, and
`UV_EXTRA_INDEX_URL` through `run_python_install()` in the image tools. The guest
requires Python 3 and a stdin-capable agent. Settings are passed over stdin,
apply only to the installer process, and are not saved in the image. This lets
release builds use the configured package mirror without changing users'
runtime package-index defaults.

## Fixing CI failures

Fix on the release branch, push, re-dispatch:

```bash
# fix the issue
git push
uv run uvr release --release-notes PACKAGE @FILE
```

For a late failure, `uvr release` supports `--reuse-run`, `--reuse-releases`,
`--skip`, and `--skip-to`. Reuse artifacts only when all required earlier builds
and checks succeeded for the intended source and versions. An early build
failure normally requires a fresh dispatch.

## Changelog

Update `CHANGELOG.md` on the release branch before dispatch. Categories: Added, Changed, Deprecated, Removed, Fixed, Security.

After publication, refresh the short **Recent releases** list before
**Installation** in the root `README.md`. Use the PyPI publication date, a
version-specific PyPI link, and one or two sentences about the release.
The `sync-readme` pre-commit hook copies the root README to
`packages/quicksand/README.md` for the package description.
