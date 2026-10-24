@AGENTS.md

Claude Code specifics:
- Spec: `IMPLEMENTATION_PLAN.md`. Model as built: `docs/MODEL.md`.
- Before any edit: read the file. Before any "done": run `pytest -q` and
  `ruff check .` and show the output.
- Use parallel tool calls for independent reads and searches.
- One step of the plan per task; stop and report when tests pass.
- Interpreters: `.venv/Scripts/python`, `.venv/Scripts/ruff`,
  `.venv/Scripts/streamlit` (system `python` is a Store stub).
- `ruff format solmu tests scripts` only; never format Markdown.
- Smoke-run the Finland dataset after core changes:
  `.venv/Scripts/python scripts/sanity_check.py` must print only PASS lines.
- Spec changes belong to the product owner; agents never edit
  `IMPLEMENTATION_PLAN.md` or `AGENTS.md`.
