"""Phase 0 entrypoint. One command runs the pipeline; one checks the feeds."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.orm import Session

from .config import Settings, load_profile, load_sources, vertex_kwargs
from .db import already_seen, engine_for, record_run
from .feedback import learned_examples, vote_counts
from .ingest import fetch_source, ingest
from .llm import LLMError
from .models import Usage
from .render import render_digest, render_funnel
from .score import score_items, score_items_heuristic
from .select import select

app = typer.Typer(add_completion=False, help="AI Radar — daily voice-AI news digest.")


def _start_by(deliver_at: str, seconds: float) -> str:
    """Clock time the trigger must fire to finish by deliver_at, plus 10 min slack."""
    from datetime import datetime, timedelta

    target = datetime.strptime(deliver_at, "%H:%M")
    return (target - timedelta(seconds=seconds + 600)).strftime("%H:%M")


console = Console()
err = Console(stderr=True)


@app.command()
def run(
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Keyword scoring instead of a model. No API key needed."
    ),
    limit: int = typer.Option(60, help="Candidates to score. Caps spend on a test run."),
    out: Path | None = typer.Option(None, help="Write the digest here as well as stdout."),
    show_rejected: int = typer.Option(0, help="Also print the top N rejected stories."),
    save: bool = typer.Option(True, help="Record the run so the dashboard can show it."),
    estimate: bool = typer.Option(
        False, "--estimate", help="Print how long the run will take, then exit."
    ),
    rescore: bool = typer.Option(
        False, "--rescore", help="Also consider stories judged in earlier runs."
    ),
    spacing: float = typer.Option(0.0, help="Seconds between API calls. 0 uses profile.yaml."),
) -> None:
    """Fetch, score, select and print this morning's digest."""
    profile = load_profile()
    source_set = load_sources()
    now = datetime.now(UTC)
    started = time.monotonic()
    engine = engine_for()

    # What the reader's votes have taught us so far. Empty until they vote, so an
    # unvoted system scores on the rubric alone rather than on invented preferences.
    with Session(engine) as session:
        learned = learned_examples(session)
        up, down = vote_counts(session)
    if learned:
        err.print(f"[dim]calibrating on {up} kept / {down} rejected from earlier runs[/dim]")

    with console.status("Fetching feeds…"):
        items, errors = ingest(source_set)
    for sid, message in errors.items():
        err.print(f"[yellow]feed failed[/] {sid}: {message}")
    if not items:
        err.print("[red]No items ingested.[/] Run `ai-radar validate` to see which feeds answered.")
        raise typer.Exit(1)

    # Drop anything an earlier run already judged. On day two onward this is most
    # of the feed, which is what keeps a paced run from growing without bound.
    if not rescore:
        with Session(engine) as session:
            seen = already_seen(session, [i.canonical_url for i in items])
        if seen:
            fresh = [i for i in items if i.canonical_url not in seen]
            err.print(f"[dim]{len(seen)} already judged in earlier runs, {len(fresh)} new[/dim]")
            items = fresh

    if not items:
        err.print("[yellow]Nothing new since the last run.[/] Use --rescore to re-judge.")
        raise typer.Exit(0)

    candidates = items[:limit]
    usage = Usage(model="none")

    # Spacing dominates runtime, so say up front how long this will take rather than
    # letting a 90-minute run look like a hang.
    gap = spacing or profile.request_spacing
    projected = (len(candidates) - 1) * gap if len(candidates) > 1 else 0.0
    if not no_llm:
        err.print(
            f"[dim]{len(candidates)} candidates, {gap:g}s apart "
            f"≈ {projected / 60:.0f} min of pacing[/dim]"
        )
        if estimate:
            deliver = profile.digest.get("deliver_at", "07:00")
            err.print(
                f"[dim]to deliver at {deliver}, start by {_start_by(deliver, projected)}[/dim]"
            )
            raise typer.Exit(0)

    if no_llm:
        scored = score_items_heuristic(candidates, profile)
    else:
        model = profile.models["scoring"]
        if not Settings().key_for(model):
            hint = (
                "Run `gcloud auth application-default login`."
                if model.startswith("vertex_ai/")
                else "Copy .env.example to .env and set the key, or run with --no-llm."
            )
            err.print(f"[red]No credential for [bold]{model}[/bold].[/] {hint}")
            raise typer.Exit(2)
        try:
            with console.status(f"Scoring {len(candidates)} stories with {model}, one at a time…"):
                scored, usage, score_errors = score_items(
                    candidates,
                    profile,
                    model,
                    extra=vertex_kwargs(profile),
                    spacing=spacing or None,
                    learned=learned,
                )
        except LLMError as exc:
            err.print(f"[red]Scoring failed:[/] {exc}")
            raise typer.Exit(1) from exc
        for message in score_errors:
            err.print(f"[yellow]item skipped[/] {message}")

    selected, rejected = select(scored, profile.story_count, profile.max_per_cluster)

    funnel = render_funnel(
        {
            "ingested": len(items),
            "scored": len(scored),
            "selected": len(selected),
        }
    )
    err.print(f"[dim]{funnel}[/dim]")

    if save:
        with Session(engine) as session:
            run_row = record_run(
                session,
                model=profile.models["scoring"] if not no_llm else "none",
                ingested=len(items),
                scored=scored,
                selected=selected,
                usage=usage,
                duration_s=time.monotonic() - started,
            )
            # Read the id inside the session; the instance detaches on exit.
            run_id = run_row.id
        err.print(f"[green]saved run #{run_id}[/] — vote at http://127.0.0.1:8000/run/{run_id}")

    digest = render_digest(selected, profile, now, usage if usage.cost_usd else None)
    console.print(digest)

    if out:
        out.write_text(digest)
        err.print(f"[green]written[/] {out}")

    if show_rejected:
        table = Table(title=f"Top {show_rejected} rejected", show_lines=False)
        table.add_column("Score", justify="right")
        table.add_column("Cluster")
        table.add_column("Title", overflow="fold")
        table.add_column("Why")
        for s in rejected[:show_rejected]:
            table.add_row(str(s.total), s.cluster_id, s.item.title[:70], s.score.rationale[:70])
        err.print(table)


@app.command()
def validate() -> None:
    """Check every configured feed and report which are usable."""
    source_set = load_sources()
    table = Table(title="Feed health")
    table.add_column("Source")
    table.add_column("Items", justify="right")
    table.add_column("Status")

    dead = 0
    for source in source_set.sources:
        items, error = fetch_source(source)
        if error:
            dead += 1
            table.add_row(source.id, "-", f"[red]{error[:60]}[/red]")
        elif not items:
            dead += 1
            table.add_row(
                source.id, "0", "[red]empty — the digest runs daily, so this is a fault[/red]"
            )
        else:
            table.add_row(source.id, str(len(items)), "[green]ok[/green]")

    console.print(table)
    if source_set.no_feed_found:
        console.print(
            f"[dim]No RSS found for: {', '.join(source_set.no_feed_found)} "
            "— these need scraping or a search API in a later phase.[/dim]"
        )
    raise typer.Exit(1 if dead else 0)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Stays local by default."),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, help="Auto-reload on code change."),
) -> None:
    """Start the dashboard: review each run, and vote the stories up or down.

    Those votes are the only thing that teaches the ranker, so the loop is
    run -> read -> vote -> next run scores against your verdicts.
    """
    import uvicorn

    console.print(f"[green]dashboard[/] http://{host}:{port}")
    uvicorn.run("ai_radar.web.app:app", host=host, port=port, reload=reload)


@app.command()
def probe(
    model: str = typer.Option("", help="Bare model id. Defaults to the scoring model."),
    locations: str = typer.Option(
        "asia-south1,global,us-central1", help="Comma-separated Vertex locations to test."
    ),
) -> None:
    """Check which Vertex regions and which routes actually serve a model.

    Region availability changes as Google rolls models out, and a 404 from a region
    looks identical to a missing permission. This answers both.
    """
    import httpx

    profile = load_profile()
    bare = model or profile.models["scoring"].split("/", 1)[-1]
    project = vertex_kwargs(profile).get("vertex_project")
    body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}

    table = Table(title=f"Availability of {bare}")
    table.add_column("Route")
    table.add_column("Result")

    if not project:
        table.add_row(
            "vertex_ai", "[red]no ADC project — run gcloud auth application-default login[/red]"
        )
    else:
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())  # type: ignore[no-untyped-call]  # google-auth is untyped
        for loc in [x.strip() for x in locations.split(",") if x.strip()]:
            host = (
                "aiplatform.googleapis.com"
                if loc == "global"
                else f"{loc}-aiplatform.googleapis.com"
            )
            url = (
                f"https://{host}/v1/projects/{project}/locations/{loc}"
                f"/publishers/google/models/{bare}:generateContent"
            )
            try:
                r = httpx.post(
                    url,
                    json=body,
                    timeout=60,
                    headers={
                        "Authorization": f"Bearer {creds.token}",
                        "Content-Type": "application/json",
                        "x-goog-user-project": project,
                    },
                )
                verdict = (
                    "[green]ok[/green]"
                    if r.status_code == 200
                    else f"[red]{r.status_code} {r.json().get('error', {}).get('status', '')}[/red]"
                )
            except Exception as exc:
                verdict = f"[red]{type(exc).__name__}[/red]"
            table.add_row(f"vertex_ai/{loc}", verdict)

    key = Settings().gemini_api_key
    if not key:
        table.add_row("gemini (api key)", "[yellow]GEMINI_API_KEY not set[/yellow]")
    else:
        import httpx as _h

        r = _h.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            timeout=45,
        )
        names = {m["name"].removeprefix("models/") for m in r.json().get("models", [])}
        table.add_row(
            "gemini (api key)",
            "[green]listed[/green]" if bare in names else "[red]not offered on this key[/red]",
        )

    console.print(table)


if __name__ == "__main__":
    app()
