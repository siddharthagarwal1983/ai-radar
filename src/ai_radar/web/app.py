"""The dashboard. Single user, local, read-mostly — server-rendered with HTMX so a
vote is one request and no client state exists to go stale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import load_profile
from ..db import Run, Story, Vote, engine_for
from ..feedback import vote_counts

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="AI Radar")
_engine = engine_for()


def get_session():  # type: ignore[no-untyped-def]
    with Session(_engine) as session:
        yield session


@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)) -> Any:
    runs = session.scalars(
        select(Run)
        .order_by(desc(Run.started_at))
        .limit(30)
        .options(selectinload(Run.stories).selectinload(Story.votes))
    ).all()
    rated = [r.precision for r in runs if r.precision is not None]
    overall = sum(rated) / len(rated) if rated else None
    recent = sum(rated[:7]) / len(rated[:7]) if rated else None
    up, down = vote_counts(session)
    totals = session.execute(select(func.sum(Run.cost_usd), func.count(Run.id))).one()
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "runs": runs,
            "up": up,
            "down": down,
            "spend": totals[0] or 0.0,
            "run_count": totals[1] or 0,
            "profile": load_profile(),
            "overall": overall,
            "recent": recent,
            "rated_runs": len(rated),
        },
    )


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: int, request: Request, session: Session = Depends(get_session)) -> Any:
    run = session.scalars(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.stories).selectinload(Story.votes))
    ).one()
    selected = [s for s in run.stories if s.selected]
    rejected = sorted(
        (s for s in run.stories if not s.selected), key=lambda s: s.total, reverse=True
    )
    return TEMPLATES.TemplateResponse(
        request,
        "run.html",
        {"run": run, "selected": selected, "rejected": rejected, "profile": load_profile()},
    )


@app.post("/vote/{story_id}", response_class=HTMLResponse)
def vote(
    story_id: int,
    request: Request,
    verdict: int = Form(...),
    note: str = Form(""),
    session: Session = Depends(get_session),
) -> Any:
    """Record a vote and re-render just that story's control.

    Voting the same way twice clears the vote, so a mis-click is undoable — the
    scorer reads these as training examples, and a wrong one quietly mis-teaches it.
    """
    story = session.scalars(
        select(Story).where(Story.id == story_id).options(selectinload(Story.votes))
    ).one()
    if story.votes and story.votes[-1].verdict == verdict and not note:
        for v in story.votes:
            session.delete(v)
        story.votes = []
    else:
        session.add(Vote(story_id=story.id, verdict=verdict, note=note, surface="web"))
    session.commit()
    session.refresh(story)
    return TEMPLATES.TemplateResponse(request, "_vote.html", {"s": story})


@app.post("/note/{story_id}")
def add_note(story_id: int, note: str = Form(""), session: Session = Depends(get_session)) -> Any:
    story = session.scalars(
        select(Story).where(Story.id == story_id).options(selectinload(Story.votes))
    ).one()
    if story.votes:
        story.votes[-1].note = note
        session.commit()
    return RedirectResponse(f"/run/{story.run_id}", status_code=303)
