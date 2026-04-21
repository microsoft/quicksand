# Dev Releases

Dev releases publish the current `.devN` version as-is, without stripping the suffix. They follow [PEP 440](https://peps.python.org/pep-0440/#developmental-releases) versioning. Use them for in-progress testing or CI integration before a stable release.

## Usage

```bash
uvr release --dev
```

## How it works

1. uvr reads each changed package's version from pyproject.toml (e.g., `1.2.3.dev0`)
2. It publishes that exact version — no stripping, no rewriting
3. After release, the pyproject.toml version is bumped to `1.2.3.dev1` (increments the dev number)

## Requirements

Every changed package **must** have a `.devN` version in its pyproject.toml. If any package has a clean version (e.g., `1.2.3` without `.devN`), uvr will error with instructions to fix it:

```
--dev release requires a .devN version in pyproject.toml, but these packages
have clean versions: my-pkg
Fix with:
  uv version 1.2.3.dev0 --directory packages/my-pkg
```

## Version ordering

Dev releases sort before all other release types for the same base version:

```
1.0.1.dev0 < 1.0.1.dev1 < 1.0.1a0 < 1.0.1 < 1.0.1.post0
```

## When to use

- **CI integration testing**: publish a dev wheel so downstream jobs or repos can test against it before the stable release
- **Early feedback**: let collaborators `pip install my-pkg==1.2.3.dev0` to try unreleased work
- **Iterative publishing**: each `--dev` release increments the dev number, so you can publish multiple times during development

## Merging

Dev release branches are typically branched from main and should be **merged back to main** after the release completes. The finalize step increments the dev number (e.g., `1.2.3.dev0` to `1.2.3.dev1`), and main needs that bump to stay in sync.

```bash
git checkout main
git pull --rebase
git merge --no-ff release/v1.2.3.dev0 -m "Merge dev release branch"
git push
```

## Tag format

```
my-pkg/v1.2.3.dev0
my-pkg/v1.2.3.dev1
```
