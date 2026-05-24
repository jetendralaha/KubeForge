# Contributing to KubeForge

Thank you for your interest in contributing to KubeForge! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Commit Messages](#commit-messages)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)

---

## Code of Conduct

This project adheres to a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the maintainers.

---

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/KubeForge.git
   cd KubeForge
   ```
3. **Add upstream** remote:
   ```bash
   git remote add upstream https://github.com/jetendra/KubeForge.git
   ```
4. **Create a branch** for your work:
   ```bash
   git checkout -b feature/your-feature-name
   ```

---

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Git
- (Optional) Docker — for container image features
- (Optional) Helm CLI — for Helm chart parsing
- (Optional) Ollama — for AI analysis features

### Install Dependencies

```bash
# Install in development mode with all extras
pip install -e ".[dev,ai,iso]"

# Start development services (Ollama + Qdrant)
make services-up
```

### Verify Setup

```bash
# Run tests
make test

# Run linter
make lint

# Run type checker
make typecheck
```

---

## How to Contribute

### Types of Contributions

- **Bug fixes** — Fix reported issues
- **Features** — Implement new functionality
- **Documentation** — Improve docs, add examples, fix typos
- **Tests** — Add missing tests or improve test coverage
- **Refactoring** — Improve code quality without changing behavior
- **Parsers** — Add support for new artifact formats (Kustomize, Pulumi, etc.)
- **AI improvements** — Better prompts, new analysis capabilities

### Good First Issues

Look for issues labeled [`good first issue`](https://github.com/jetendra/KubeForge/labels/good%20first%20issue) — these are specifically curated for new contributors.

---

## Pull Request Process

1. **Update your branch** with the latest upstream changes:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Ensure all checks pass**:
   ```bash
   make lint
   make typecheck
   make test
   ```

3. **Write/update tests** for your changes

4. **Update documentation** if your changes affect the public API or configuration

5. **Submit the PR** with a clear description of:
   - What the change does
   - Why it's needed
   - How it was tested
   - Any breaking changes

6. **Address review feedback** — maintainers may request changes

### PR Requirements

- [ ] All tests pass
- [ ] Code follows project style (enforced by `ruff`)
- [ ] Type annotations are complete (`mypy --strict` passes)
- [ ] New features include tests
- [ ] Documentation is updated where applicable
- [ ] Commit messages follow the convention

---

## Coding Standards

### Style

- **Formatter**: [Ruff](https://docs.astral.sh/ruff/) (line length: 120)
- **Linter**: Ruff with rules `E`, `F`, `I`, `N`, `W`, `UP`
- **Type checker**: mypy (strict mode)
- **Python version**: 3.11+ (use modern syntax: `X | Y` unions, `match` statements, etc.)

### Conventions

```python
# Use type annotations everywhere
def create_bundle(
    project_name: str,
    manifests: dict[str, str],
    images: list[str],
    output_dir: str = "",
) -> str:
    """Create an offline deployment bundle.

    Args:
        project_name: Name of the project.
        manifests: Generated K8s manifest files {filename: content}.
        images: List of container image references to include.
        output_dir: Override output directory.

    Returns:
        Path to the created bundle archive.

    Raises:
        PackageError: If bundling fails.
    """
    ...
```

- Use `from __future__ import annotations` in all modules
- Prefer `async/await` for I/O operations
- Use `logging` module (not `print`) for operational messages
- Use `pydantic` for data validation and models
- Keep functions focused — one responsibility per function

### File Organization

- One class per file (for large classes) or logically grouped small utilities
- Module docstrings explaining purpose at the top of every file
- Imports ordered: stdlib → third-party → local (enforced by Ruff)

---

## Testing

### Running Tests

```bash
# Full test suite with coverage
make test

# Verbose output
make test-verbose

# Specific test file
pytest tests/test_generator.py -v

# Specific test function
pytest tests/test_generator.py::test_generates_namespace -v
```

### Writing Tests

- Use `pytest` with `pytest-asyncio` for async tests
- Place tests in `tests/` mirroring source structure
- Use descriptive test names: `test_compose_parser_handles_missing_services`
- Use fixtures for shared setup
- Test both success and error paths

```python
import pytest
from kubeforge.parsers.compose import ComposeParser

@pytest.fixture
def parser():
    return ComposeParser()

def test_detect_returns_high_confidence_for_compose_file(parser):
    content = "services:\n  web:\n    image: nginx"
    score = parser.detect(content, "docker-compose.yml")
    assert score >= 0.8

@pytest.mark.asyncio
async def test_parse_extracts_workloads(parser):
    content = "services:\n  web:\n    image: nginx:latest\n    ports:\n      - '80:80'"
    result = await parser.parse(content)
    assert len(result.workloads) == 1
    assert result.workloads[0].name == "web"
```

### Test Data

Place test fixtures in `tests/testdata/` organized by format:
```
tests/testdata/
├── compose/
├── kube/
└── helm/
```

---

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation changes |
| `style` | Code style (formatting, no logic change) |
| `refactor` | Code change (no feature/fix) |
| `perf` | Performance improvement |
| `test` | Add/fix tests |
| `chore` | Build process, tooling, etc. |

### Examples

```
feat(parser): add Kustomize overlay parser

Implements parsing of kustomization.yaml files and base/overlay
directory structures. Converts overlays into the NormalizedManifest IR.

Closes #42
```

```
fix(generator): correct network policy port matching

Previously, named ports in services were not matched correctly
in generated NetworkPolicy ingress rules.
```

---

## Reporting Bugs

Use the [GitHub Issues](https://github.com/jetendra/KubeForge/issues) tracker with the `bug` label.

Include:
- **KubeForge version** (`kubeforge version`)
- **Python version** (`python --version`)
- **Operating system**
- **Steps to reproduce**
- **Expected behavior**
- **Actual behavior**
- **Relevant logs/output**

---

## Requesting Features

Open an issue with the `enhancement` label. Describe:
- **Use case** — What problem does this solve?
- **Proposed solution** — How should it work?
- **Alternatives considered** — Other approaches you thought of
- **Additional context** — Screenshots, references, examples

---

## License

By contributing to KubeForge, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

Thank you for helping make KubeForge better!
