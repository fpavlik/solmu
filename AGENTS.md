# AGENTS.md — rules for AI coding agents working in this repository

You are working on an open-source supply-chain resilience simulator
(headless Python core + Streamlit UI). The full specification is in
`IMPLEMENTATION_PLAN.md`; the model as actually implemented is documented in
`docs/MODEL.md`. Read both before changing anything. This file holds the
rules of conduct; it does not repeat the spec.

## 1. Start of every task

1. Read `IMPLEMENTATION_PLAN.md` sections 2, 3, 10 and 13 (principles,
   layout, extension points, realism extensions). They are constraints,
   not suggestions. Never edit `IMPLEMENTATION_PLAN.md` or this file; the
   product owner changes the spec, agents record what was built in
   `docs/MODEL.md`.
2. Read `docs/MODEL.md` and the files you will touch. Never edit a file you
   have not read in this session.
3. Restate the task in two or three lines, name the files you expect to
   change, and list assumptions. If an assumption affects the model (units,
   priorities, formulas), stop and ask before coding.
4. Work on one step of `IMPLEMENTATION_PLAN.md` section 12 at a time.
   Do not start the next step until tests for the current one pass.

## 2. Searching and verifying code

- Use `rg` (ripgrep) to find usages before renaming or changing a
  signature: `rg "def step\(" solmu/`, `rg -n "unmet" solmu/ tests/`.
  Grep for every call site; do not assume there is only one.
- Use `tree -I '__pycache__|.venv' -L 3` to orient yourself.
- Do not guess library APIs. For `networkx`, `pydantic`, `pandas`,
  `streamlit`, `pydeck`: check the installed version
  (`pip show <pkg>`) and read the docs or `help()` output before using an
  unfamiliar function. networkx `min_cost_flow` requires integer
  capacities and costs — this has bitten before.
- Environment (Windows): the `python` on PATH is a Microsoft Store stub.
  Always use `.venv/Scripts/python`, `.venv/Scripts/ruff`,
  `.venv/Scripts/streamlit`. Run `ruff format` on Python paths only
  (`ruff format solmu tests scripts`), never on the repository root: it
  reformats code blocks inside Markdown, including the spec.
- Before claiming anything works, run it: `pytest -q`, `ruff check .`,
  and for UI changes `streamlit run solmu/ui/app.py` with a real scenario.
  "Should work" is not a status. Paste the actual command output in your
  report.
- When a test fails, read the full traceback and fix the cause. Never
  weaken an assertion, add `skip`, or widen a tolerance to make a test
  pass; if a test is genuinely wrong, say so and explain why.
- Verify numbers against the hand-computed expectations in
  `tests/test_engine_small.py`. If a change in the model moves those
  numbers, that is a model change and must be recorded in `docs/MODEL.md`.

## 3. Parallelism

- Independent reads and searches (reading several files, several `rg`
  calls, `pip show` for several packages) should be issued together, not
  one after another.
- In code, batch runs (`core/batch.py`) may use `concurrent.futures`
  with processes, but each `Simulation` must stay single-threaded and
  deterministic. Never share mutable state between runs.
- Do not parallelize edits to the same file.

## 4. Code conventions

- Python 3.11+, type hints on every public function and method in
  `solmu/core`. `ruff check` and `ruff format` must be clean.
- **Comments:** only where the *why* is non-obvious (a modelling choice, a
  workaround, a non-trivial invariant). No comments that restate the code
  (`# increment day`), no section banners, no commented-out code, no
  TODOs without an owner and a reason. Public classes and functions get a
  short docstring (one to three lines). That is all.
- Prefer small pure functions; the tick order in `engine.py` should read
  as a list of named steps.
- No globals, no module-level mutable state, no singletons.
- No Finland-specific identifiers or numbers in Python code. Everything
  country-specific lives in `data/`.
- `solmu/core` imports nothing from `solmu/ui`, `scripts/` or any
  plotting/HTTP library. Run `rg "import streamlit|import pydeck|import plotly" solmu/core` — it must return nothing.
- New dependency: only if it removes real work; name it in the report and
  add it to `pyproject.toml` with a lower bound. Do not add a dependency for
  a single helper function.
- Errors: validation errors must name the file, record and field. Do not
  catch broad exceptions to keep going; fail loudly.
- Keep the public interface from `IMPLEMENTATION_PLAN.md` section 6.1
  stable. If you believe it must change, propose the change in the report
  first.

## 4a. New model mechanisms

- Every new mechanism is opt-in through data fields with defaults that
  reproduce the previous behaviour exactly. Existing tests must pass
  unchanged; a mechanism that needs an existing assertion changed is a model
  change and goes through the product owner first.
- Country-specific behaviour (which commodities go through warehouses, port
  headroom, which edges carry what) is expressed in `data/`, never in code.
- The abstraction level is aggregate: capacities, stocks, throughput, delays.
  Do not model individual vehicles, ships, orders or firms.
- Hand-compute the expected numbers for at least one small case of every new
  mechanism and write the arithmetic into the test.

## 5. Data rules

- Every number in `data/` has a line in `data/finland/SOURCES.md` with
  source and confidence (`measured` / `estimated` / `placeholder`).
  No exceptions, including placeholders.
- Do not "improve" data to make a scenario look better without recording
  the reasoning in `SOURCES.md`.
- Never commit sensitive or non-public data. The repository is public.

## 6. Scope discipline

Do not build anything from `IMPLEMENTATION_PLAN.md` section 1.3 (OSM
import, HTTP API, agent-based simulation),
even if it seems easy or "while I'm here". If you see a cheap opportunity,
mention it in the report instead.

Do not refactor files outside the task. Do not rename things for taste.

## 7. Reporting a finished step

Keep it short and factual:

1. What changed (files, one line each).
2. Commands run and their actual results (test count, ruff status).
3. Modelling decisions or simplifications made, and whether
   `docs/MODEL.md` was updated.
4. Open questions or deviations from the plan.

No summaries of what the code "will enable", no praise of the solution.

## 8. If you are unsure

Ask a specific question with the options you see, or pick the simpler
option, implement it behind the existing interface, and document the
assumption in `docs/MODEL.md`. Never silently invent a formula.
