# Contributing to Laya

Thanks for your interest in contributing to Laya! This guide will help you get
started.

## Getting Started

### Prerequisites

- **Python 3.11+** with pip
- **Node.js 20+** with npm
- **Rust** (latest stable) with cargo
- **n8n** installed globally (`npm install -g n8n`)

### Setup

```bash
# Clone the repo
git clone https://github.com/AayushChawla/laya.git
cd laya

# Run the setup script (creates venvs, installs deps)
scripts/setup-dev.sh

# Start development
scripts/dev.sh
```

### Project Structure

| Directory | Description |
|-----------|-------------|
| `engine/laya/` | Python FastAPI backend (port 8420) |
| `ui/src/` | SvelteKit + Svelte 5 frontend (port 5173) |
| `ui/src-tauri/` | Tauri v2 Rust shell |
| `n8n/workflows/` | Bundled n8n workflow definitions |
| `scripts/` | Dev, build, and setup scripts |

## Development Workflow

1. **Fork** the repository and create a feature branch from `main`.
2. **Make your changes** following the conventions below.
3. **Test** your changes (see Testing section).
4. **Open a pull request** against `main`.

### Branch Naming

Use descriptive branch names:

- `feat/short-description` for features
- `fix/short-description` for bug fixes
- `docs/short-description` for documentation

### Commit Messages

Write clear, concise commit messages:

- Start with a verb in imperative mood (`Add`, `Fix`, `Update`, `Remove`)
- Keep the first line under 72 characters
- Add a blank line and details if needed

## Code Conventions

### Engine (Python)

- **Async everywhere**: all DB access, HTTP calls, and pipeline functions must be
  async
- **SQLite migrations**: numbered files in `engine/laya/db/migrations/`. New
  migrations get the next sequential number.
- **LLM prompts**: organized by role in `engine/laya/llm/prompts/`
- **Tests**: use pytest with async fixtures from `engine/tests/conftest.py`

### UI (Svelte)

- **Svelte 5 runes only**: use `$state`, `$derived`, `$effect`, `$props` —
  never legacy `$:` reactive declarations
- **Skeleton UI v4 + Tailwind CSS v4** for styling
- **No emoji icons** in UI elements — use SVG icons or nothing

### General

- Don't introduce abstractions beyond what the task requires
- Don't add speculative error handling for impossible scenarios
- Comments only when the *why* is non-obvious

## Testing

### Backend

```bash
cd engine && source .venv/bin/activate

# Run all tests
pytest

# Run a specific test file
pytest tests/test_cards_api.py

# Run a single test
pytest tests/test_cards_api.py::test_function_name -v
```

### Frontend

```bash
cd ui

# Type checking
npx svelte-check

# Dev server
npm run dev
```

## Reporting Issues

- Use the **Bug Report** template for bugs
- Use the **Feature Request** template for enhancements
- Search existing issues first to avoid duplicates

## Pull Requests

- Fill out the PR template completely
- Link related issues using `Closes #123` or `Fixes #123`
- Keep PRs focused — one feature or fix per PR
- Ensure tests pass before requesting review

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](../LICENSE).
