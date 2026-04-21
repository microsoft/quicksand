# `uvr skill init`

Copy bundled Claude Code skills into your project.

```bash
uvr skill init                   # write skills to .claude/skills/
uvr skill init --force           # overwrite existing skill files
```

Copies the release skill and its references into `.claude/skills/` in the current directory. Existing files are skipped unless `--force` is used.
