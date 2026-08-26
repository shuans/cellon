# Cellon Publishing Guide

Cellon is built and published by GitHub Actions. No local Rust, wheel, or source-distribution build is required.

## Prerequisites

1. A GitHub repository containing this project.
2. A PyPI project named `cellon` or permission to create it.
3. A GitHub environment named `pypi` (recommended for protecting production releases).
4. A PyPI API token stored as the GitHub Actions secret `PYPI_API_TOKEN`.
5. Optionally, a Test PyPI API token stored as `TEST_PYPI_API_TOKEN`.

## PyPI API Token

Create a project-scoped API token in PyPI and add it to the repository or `pypi`
environment as the secret `PYPI_API_TOKEN`. The publish workflow authenticates
with username `__token__` and this secret; the token value is never committed.

For Test PyPI, create a separate token at Test PyPI and store it as
`TEST_PYPI_API_TOKEN`. Do not reuse the production token.

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

### API token authentication fails

Check that `PYPI_API_TOKEN` is configured in the repository or `pypi` environment,
that the token is scoped to the `cellon` project, and that the workflow uses the
`__token__` username. For Test PyPI, verify `TEST_PYPI_API_TOKEN` separately.

### Version already exists

PyPI versions are immutable. Bump the version in both `Cargo.toml` and `pyproject.toml`, then publish a new GitHub Release.

### CI build fails

Review the failed workflow logs. The repository intentionally does not build release artifacts on the developer machine; the GitHub runner is the source of published artifacts.
