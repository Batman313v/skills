---
name: python
description: Python patterns, best practices, and idioms. Use when writing Python code.
---

## python

Write idiomatic Python.

### Tooling — ALWAYS use uv
For ALL Python projects, `uv` is the primary tool for everything:
- **Dependency management**: `uv add <pkg>` / `uv remove <pkg>` / `uv sync`
- **Running code**: `uv run python script.py` or `uv run pytest`
- **Running tools**: `uv run ruff check .`, `uv run ty check src/`
- **Scripts with deps**: `uv run --with requests python script.py`
- **Creating projects**: `uv init myproject` / `uv init --package myproject`

Never use: `pip install`, `pip freeze`, `python -m venv`, `source .venv/bin/activate`. Let subagents discover and follow existing project conventions if they already have a different setup.

### Style
- PEP 8 (black or ruff)
- Type hints for all signatures
- Docstrings (Google or NumPy style)
- pathlib over os.path
- f-strings for formatting

### Common patterns
- Context managers (with)
- Dataclasses for data
- Enums for constants
- Protocol for duck typing
- match/case (3.10+)
