# AI Radar — start here

**Read `session.md` first, then `project_memory.md`, before doing anything else.**

- `session.md` — where we left off, the current milestone, what to do next. Changes often.
- `project_memory.md` — decisions already made and why. Changes rarely. **Do not re-open
  anything settled there without saying why it should change.**

Update `session.md` at the end of any session that changes the state of the project.
Add to `project_memory.md` only when a *decision* is made or a fact is *measured* —
not for routine work.

## Working agreements

- Python 3.12+, managed with `uv`. Run things as `uv run ...`, never bare `python`.
- **No vendor LLM SDK.** Every model call goes through `llm.py` → LiteLLM. This is
  deliberate; see `project_memory.md`.
- Verify before claiming. This project has had three silent no-op patches caught only
  because a test or a live run checked the result. Run the thing.
- `ruff check`, `ruff format`, `mypy src/ai_radar`, `pytest` all pass before stopping.
- Never print, log, or commit the contents of `.env`.
