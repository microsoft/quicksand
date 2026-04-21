# Changelog

## [v0.1.7] - 2026-03-31

### Fixed
- Slimmed overlay image by ~160 MB by removing unused Playwright caches, headless shell, Vulkan drivers, and Node.js runtime
- Export `AifCuaAgentSandbox` from package root so the documented import works
- Fix stale `__version__` string in `__init__.py`

### Changed
- Updated README to document noVNC web client on port 6080
