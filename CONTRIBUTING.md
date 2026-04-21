# Contributing to Quicksand

## Development Setup

```bash
git clone https://github.com/microsoft/quicksand
cd quicksand
uv sync --all-packages --all-groups --all-extras
```

Run checks and tests:
```bash
uv run poe ci
```

## Guides

| Guide | What it covers |
|-------|---------------|
| [Creating Images](docs/contributor-guide/01-creating-images.md) | Build a new base or overlay image package |
| [Extending the Sandbox](docs/contributor-guide/02-extending-the-sandbox.md) | Add a method, OS, architecture, or QEMU flag |
| [Testing](docs/contributor-guide/03-testing.md) | Run or write tests |
| [Releasing](docs/contributor-guide/04-releasing.md) | Cut a release |

See the full [Contributor Guide](docs/contributor-guide/).

## Code Style

- Format with `ruff format`
- Lint with `ruff check`
- Type check with `ty check`
- Run `uv run poe ci` before submitting PRs

## Questions?

Open an issue on GitHub.
