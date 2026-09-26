# Contributing to pycrestron-cip

Thank you for your interest in contributing! This document explains the workflow and guidelines.

## Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable release branch. Protected — no direct pushes. |
| `development` | Integration branch. All feature work merges here first. |
| `feature/*` | Short-lived branches for individual changes. |

## Workflow

1. **Create a feature branch** from `development`:
   ```bash
   git checkout development && git pull
   git checkout -b feature/my-change
   ```

2. **Make your changes**, commit, and push:
   ```bash
   git push origin feature/my-change
   ```

3. **Open a Pull Request** targeting `development`. CI (lint + tests) must pass before merging.

4. **When ready to release**, open a Pull Request from `development` → `main`. CI runs again.

5. **On merge to `main`**, a release is created automatically:
   - Version in `__version__.py` is bumped from the PR label
   - A git tag and GitHub release are created
   - Add exactly one of `patch`, `minor`, or `major` to the PR before merging

## Development Setup

```bash
# Clone the repo
git clone https://github.com/Antonio112009/pycrestron-cip.git
cd pycrestron-cip

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

## Linting

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check .
ruff format --check .
```

## Code Style

- Python 3.13+, no runtime dependencies: the library must stay easy to install inside Home Assistant
- Line length: 120 characters
- Follow existing patterns in the codebase
- Add tests for new functionality. Byte vectors should come from real captures where possible;
  say which processor they come from
- Never commit captures that contain addresses, passwords or names from a real installation

## Project Structure

```
src/pycrestron_cip/
├── client.py              # CipClient: connection, sync, heartbeats, reconnect, join caches
├── protocol.py            # Frame encoding/decoding (no I/O)
├── exceptions.py          # Exception hierarchy
└── testing.py             # FakeProcessor: a CIP server for tests
docs/protocol.md           # Wire format notes and hardware findings
examples/probe.py          # Read-only probe: record what a processor sends
```

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
