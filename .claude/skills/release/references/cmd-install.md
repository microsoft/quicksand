# `uvr install`

Install a package directly from GitHub releases.

```bash
uvr install ORG/REPO/PKG              # latest version
uvr install ORG/REPO/PKG@1.2.3        # specific version
```

Downloads the wheel from the GitHub release tagged `PKG/v1.2.3` and installs it with `uv pip install`. When no version is specified, finds the latest release tag for the package.
