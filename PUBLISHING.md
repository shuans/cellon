# Cellon Publishing Guide

Cellon is built and published by GitHub Actions. No local Rust, wheel, or source-distribution build is required.

## Prerequisites

1. A GitHub repository containing this project.
2. A PyPI project named `cellon` or permission to create it.
3. A GitHub environment named `pypi`.
4. A PyPI Trusted Publisher configured for the repository and workflow.

## PyPI Trusted Publishing

Configure the publisher at PyPI under **Publishing** with:

- Owner: the GitHub organization or user that owns the repository
- Repository: the repository name
- Workflow name: `.github/workflows/publish.yml`
- Environment: `pypi`

For Test PyPI, configure a second publisher with the same repository and workflow, using the environment selected by your Test PyPI policy.

The workflow requests the `id-token: write` permission and uses `pypa/gh-action-pypi-publish`; no `PYPI_API_TOKEN` secret is needed.

## Production Release

1. Update the version in both `Cargo.toml` and `pyproject.toml`.
2. Commit and push the version change.
3. Create and publish a GitHub Release.
4. GitHub Actions builds the sdist and platform wheels, then publishes them to `https://pypi.org/project/cellon/`.

The production publish job only runs for a published GitHub Release.

## Test PyPI

Start the `Publish Cellon to PyPI` workflow manually from GitHub Actions and set `publish_to_test_pypi` to `true`. The workflow publishes the artifacts to Test PyPI instead of production PyPI.

## Installation

```bash
pip install cellon
```

The distribution is named `cellon`. The historical Python import namespace `cello` remains available, so both forms work:

```python
from cellon import App
# Existing applications may continue to use:
from cello import App
```

## CI Build Matrix

The workflow builds on GitHub-hosted runners for:

- Linux x86_64 and aarch64
- macOS aarch64
- Windows x86_64
- Source distribution

The native extension keeps the `cello._cello` module name for compatibility with existing applications.

## Troubleshooting

### Trusted publishing fails

Check that the PyPI publisher exactly matches the GitHub owner, repository, workflow path, and `pypi` environment. Also verify that the job has `id-token: write` permission.

### Version already exists

PyPI versions are immutable. Bump the version in both `Cargo.toml` and `pyproject.toml`, then publish a new GitHub Release.

### CI build fails

Review the failed workflow logs. The repository intentionally does not build release artifacts on the developer machine; the GitHub runner is the source of published artifacts.
