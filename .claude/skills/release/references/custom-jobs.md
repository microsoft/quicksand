# Adding Custom Jobs

The workflow model allows extra jobs (`extra="allow"`), so you can add any job to `release.yml` and wire it into the pipeline via `needs`.

## Running checks before build

Read the project's existing CI workflows (e.g., `ci.yml`, `test.yml`) to understand what checks run today. Then add those as a job in `release.yml`:

```yaml
checks:
  runs-on: ubuntu-latest
  if: ${{ !contains(fromJSON(inputs.plan).skip, 'checks') }}
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v6
    - run: uv sync --all-packages
    - run: uv run poe check
    - run: uv run poe test

build:
  needs: [checks]         # build waits for checks to pass
  # ... (rest of build job unchanged)
```

## Running actions after bump

For example, publishing to PyPI:

```yaml
pypi-publish:
  needs: [bump]
  runs-on: ubuntu-latest
  if: ${{ always() && !failure() && !cancelled() && !contains(fromJSON(inputs.plan).skip, 'pypi-publish') }}
  environment: pypi
  steps:
    # ...
```

## Tips

- **Use the skip mechanism.** Add `if: ${{ !contains(fromJSON(inputs.plan).skip, '<job-name>') }}` so the job can be skipped at release time with `uvr release --skip <job-name>`.
- **Gate core jobs via `needs`.** If your custom job should block a core job, add it to that core job's `needs` list.
- **Run `uvr workflow validate`** after editing to check for schema errors.
- **Use `always() && !failure() && !cancelled()`** in the `if` condition for jobs that follow skippable jobs. Without this, a skipped upstream job causes the downstream job to be skipped too.
