# Contributing to HydraDB CLI

Thank you for your interest in contributing to HydraDB CLI! This guide will help you get started.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Branch Naming Convention](#branch-naming-convention)
- [Commit Message Format](#commit-message-format)
- [Developer Certificate of Origin (DCO)](#developer-certificate-of-origin-dco)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)

---

## Getting Started

### Prerequisites

- Python 3.10 or later
- Git

### Quick setup

```bash
git clone https://github.com/hydra-db/hydradb-cli.git
cd hydradb-cli
make bootstrap
source .venv/bin/activate
```

This creates a virtual environment, installs all dependencies (including dev tools like ruff and pytest), and makes the `hydradb` command available.

### Manual setup

If you prefer to do it manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Verify your setup

```bash
hydradb --version          # CLI works
make lint                  # linter passes
make test                  # all tests pass
```

---

## Branch Naming Convention

Create a new branch from `main` for every change. Use the following prefixes:

- `feat/` -- new features (e.g., `feat/batch-upload`)
- `fix/` -- bug fixes (e.g., `fix/timeout-handling`)
- `docs/` -- documentation changes (e.g., `docs/update-readme`)
- `chore/` -- maintenance, CI, and tooling (e.g., `chore/update-dependencies`)

---

## Commit Message Format

This project follows the [Conventional Commits](https://www.conventionalcommits.org/) specification.

### Format

```
<type>(<scope>): <description>

[optional body]

[optional footer]
Signed-off-by: Your Name <your.email@example.com>
```

### Types

| Type | Description |
|------|-------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `style` | Formatting, missing semicolons, etc. |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or correcting tests |
| `chore` | Maintenance tasks (CI, build, deps) |

### Examples

```
feat(recall): add --top-k flag to full recall command

fix(client): handle 429 rate-limit responses with retry-after header

docs: update installation instructions for Python 3.13
```

---

## Developer Certificate of Origin (DCO)

All commits must be signed off to certify that you wrote or have the right to submit the code. Add a sign-off line to every commit:

```bash
git commit -s -m "feat(recall): add --top-k flag"
```

This adds a `Signed-off-by: Your Name <email>` trailer. CI will reject commits without it.

If you forget, amend the most recent commit:

```bash
git commit --amend -s
```

Or sign off all commits in a PR:

```bash
git rebase --signoff main
```

---

## Code Style

Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/). It is installed automatically with `make bootstrap` or `pip install -e ".[dev]"`. Run before committing:

```bash
make lint     # check for issues
make format   # auto-fix formatting
```

### Key rules

- **Line length:** 120 characters (enforced by ruff formatter)
- **Import sorting:** handled by ruff's isort rules
- **Type hints:** use them for all public function signatures
- **Docstrings:** required for all public functions and classes

### Ruff configuration

The full ruff configuration lives in `pyproject.toml` under `[tool.ruff]`. The selected rule sets are:

| Rule | Description |
|------|-------------|
| E/W | pycodestyle errors and warnings |
| F | pyflakes |
| I | isort (import sorting) |
| N | pep8-naming |
| UP | pyupgrade |
| B | flake8-bugbear |
| S | flake8-bandit (security) |
| T20 | flake8-print |
| SIM | flake8-simplify |

---

## Testing

The test suite uses [pytest](https://docs.pytest.org/) with [typer.testing.CliRunner](https://typer.tiangolo.com/tutorial/testing/) for CLI integration tests.

### Running tests

```bash
make test                  # quick run
make coverage              # with coverage report
pytest -v                  # verbose output
pytest tests/test_client.py -v   # run a specific test file
```

### Writing tests

- Place test files in `tests/` with the `test_` prefix
- Use `typer.testing.CliRunner` for CLI command tests
- Mock `HydraDBClient` methods to avoid real API calls
- Follow the existing patterns in `tests/test_cli_commands.py`

### Test structure

```
tests/
├── test_cli_commands.py   # CLI integration tests (CliRunner)
├── test_client.py         # HTTP client unit tests
├── test_config.py         # Configuration management tests
└── test_output.py         # Output formatting tests
```

---

## Pull Request Process

1. **Create a branch** from `main` using the naming convention above
2. **Make your changes** in small, focused commits
3. **Run the checks** before pushing:
   ```bash
   make lint
   make test
   ```
4. **Push and open a PR** against `main`
5. **Fill out the PR template** -- describe what changed and why
6. **Ensure CI passes** -- all status checks must be green
7. **Request review** -- a maintainer will review your PR

### PR checklist

- [ ] Code follows the project's style guidelines (`make lint` passes)
- [ ] Tests pass (`make test` passes)
- [ ] New code has corresponding tests
- [ ] All commits are signed off (DCO)
- [ ] PR description explains the change

---

## Reporting Issues

Use [GitHub Issues](https://github.com/hydra-db/hydradb-cli/issues) to report bugs or request features. Please use the provided templates:

- **Bug Report** -- for unexpected behavior or errors
- **Feature Request** -- for new functionality ideas

Before opening a new issue, search existing issues to avoid duplicates.

---

## Questions?

If you have questions about contributing, open a [Discussion](https://github.com/hydra-db/hydradb-cli/discussions) or reach out to the maintainers.

Thank you for helping make HydraDB CLI better!
