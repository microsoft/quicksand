# Releasing

Releases are managed via the `/release` Claude Code skill.

## Flow

1. **Dry run.** Run `uv run poe release:dry-run` to preview what will be built.
2. **Branch.** Create `release/v{version}` from main.
3. **Dispatch.** Run `gh workflow run release.yml --ref release/v{version}`.
4. **Monitor.** CI runs check, build, overlay build, test, and release.
5. **Post-release.** Verify per-package releases and test install.

## Versioning

| Bump | When |
|------|------|
| **Patch** (automatic) | Bug fixes, CI fixes, doc updates, refactors |
| **Minor** (manual) | New features, new public API, breaking changes, new packages |

Patch versions are set automatically by the previous release's dev bump.

## Per-package releases

Each package gets its own GitHub release (`quicksand-core/v0.7.0`, `quicksand-qemu/v0.4.0`, etc.). Unchanged packages keep their existing releases. Change detection and internal dependency pinning are handled automatically by `quicksand-plan-release`.

## Fixing CI failures

Fix on the release branch, push, re-dispatch:

```bash
git checkout release/v<VERSION>
# fix the issue
git push origin release/v<VERSION>
gh workflow run release.yml --ref release/v<VERSION>
```

To reuse builds from a previous run:
```bash
gh workflow run release.yml --ref release/v<VERSION> \
  -f plan="$(uv run quicksand-plan-release --reuse-base-build <RUN_ID> --reuse-overlay-build <RUN_ID>)"
```

## Changelog

Update `CHANGELOG.md` on the release branch before dispatch. Categories: Added, Changed, Deprecated, Removed, Fixed, Security.
