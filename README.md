# AI Radar

A daily digest of voice-AI news, ranked against a personal interest profile and
delivered at 07:00 IST. Architecture and plan:
[AI Radar — Architecture & Implementation Plan](https://claude.ai/code/artifact/c586d172-ae74-4d5c-aa74-1db9590ed85f)

**Status:** pipeline runs end to end with persistence, a voting dashboard, and a
working feedback loop. Telegram delivery and summarization are not built yet.

- **`session.md`** — where work stopped, the current milestone, what to do next.
- **`project_memory.md`** — decisions already made, and the measurements behind them.
- **`CLAUDE.md`** — makes Claude read both at the start of every session.

## Run it

```bash
uv sync
uv run ai-radar validate     # check every feed
uv run ai-radar run          # fetch, score, select, print — and save the run
uv run ai-radar serve        # dashboard at http://127.0.0.1:8000 — vote there
```

The loop is **run → read → vote → next run scores against your verdicts.**

## Daily routine, for the manual trial

```bash
caffeinate -i uv run ai-radar run --limit 40    # ~60 min; keeps the Mac awake
uv run ai-radar serve                            # read and vote
```

`caffeinate -i` matters: a 40-candidate run paces for about an hour, and a Mac that
sleeps mid-run loses it. Everything scored before the interruption is lost — the run
is only written at the end.

**Vote on everything delivered, every day, including the ones you feel neutral about.**
An unvoted story teaches nothing, and the dashboard's precision figure only counts
stories you judged.

The headline number on the dashboard is **kept, last 7** — the share of delivered
stories you kept. That is the efficacy measure: a digest is working when most of what
it sends is worth keeping. Watch whether it climbs against **kept, all runs**; if it
has not moved after a week of voting, the rubric weights in `profile.yaml` need
changing, not more votes.

Each run only judges stories it has not seen before, so day two onward is much shorter
than day one. `--rescore` re-judges old stories, which is what you want after editing
the rubric.

With a model (copy `.env.example` to `.env` and set one key first):

```bash
uv run ai-radar run --show-rejected 10
```

| Flag | Does |
| --- | --- |
| `--no-llm` | Keyword scoring instead of a model. Useful offline; not a ranker. |
| `--limit N` | Candidates to score. Caps spend on a test run. Default 60. |
| `--show-rejected N` | Print what was dropped and why. The fastest way to spot a bad rubric. |
| `--out FILE` | Also write the digest to a file. |

## Layout

```
config/profile.yaml    interest profile: clusters, weights, rubric, models
config/sources.yaml    feeds, validated 2026-10-04
src/ai_radar/
  llm.py               the only place a model is called (LiteLLM, no vendor SDK)
  ingest.py            fetch, normalise, canonicalise URLs, dedupe
  score.py             rubric prompt + parallel scoring; keyword fallback
  select.py            top-N with the per-cluster diversity cap
  render.py            canonical Markdown that every sink renders from
  cli.py               entrypoint
```

## The feedback loop

Every run is saved to SQLite with all its candidates, scores, rationales, and what
was rejected. The dashboard shows each run; **Keep** and **Not for me** on any story
record a vote, and an optional note explains why.

Those votes are the entire learning mechanism. Before the next run, the most recent
verdicts are replayed into the scoring prompt as worked examples, so the model
calibrates against you rather than against a generic notion of "interesting". No
fine-tuning, no embedding store — which means you can read exactly what the model was
told, and deleting a vote undoes its influence.

Measured on 2026-10-04, one down-vote moved a story from a stable 50–54 across three
runs to 0.0 on the next:

| Story | run 1 | run 2 | run 3 | vote | run 4 |
| --- | --- | --- | --- | --- | --- |
| Looped Audio Spectrogram Transformer | 53.8 | 50.0 | 52.0 | 👎 | **0.0** |
| Multi-sample Synthetic Supervision | 85.0 | 85.0 | 85.0 | 👍 | **85.0** |

**That responsiveness cuts both ways.** With a handful of votes the signal is very
strong and a mis-click teaches the wrong lesson, so voting the same way twice clears
the vote. Expect it to moderate as votes accumulate; if it over-corrects early, cut
`per_side` in `feedback.learned_examples`.

Clicking the same verdict twice removes the vote entirely.

## Two things to know

**Clusters are capabilities, not companies.** `profile.yaml` describes what a
story must *do* — a latency improvement, a new language, a better interruption
model — never who shipped it. A test asserts no vendor name reaches the scoring
prompt, because a keyword list of vendors quietly becomes a watchlist and ranks
familiar press releases above the release that matters from an unfamiliar lab.

**Weights are applied here, not by the model.** The model returns five subscores;
`Score.weighted_total` combines them using the rubric in `profile.yaml`. Retuning
the weights needs no prompt change and no re-run.

## Models and credentials

`config/profile.yaml` → `models.scoring` / `models.summarizing`. Current model is
**`gemini-3.1-flash-lite`** on Vertex AI.

### Cost: Vertex AI vs Gemini API key

Identical workload, three stories, same prompt, measured 2026-10-04:

| Route | Tokens | Total | Per story |
| --- | --- | --- | --- |
| `vertex_ai/gemini-3.1-flash-lite` (global) | 2,511 in / 359 out | $0.001166 | **$0.000389** |
| `gemini/gemini-3.1-flash-lite` (API key) | 2,514 in / 365 out | $0.001176 | **$0.000392** |

**Per-token pricing is the same on both routes.** The 0.8% gap is token-count
variance between two runs, not a rate difference. The choice is therefore about
quota and billing, not price:

| | Vertex AI (ADC) | Gemini API key |
| --- | --- | --- |
| Credential | `gcloud auth application-default login` | `GEMINI_API_KEY` in `.env` |
| Billing | GCP project | Google AI Studio account |
| Free tier | none — project billing applies | 20 requests/day/model |
| Usable for a 60-story run | yes | only with billing enabled |
| Region control | yes, where the model is deployed | no |

At $0.00041 a story, a 60-candidate run costs about **$0.025**, roughly **$0.75 a
month** — five times cheaper than `gemini-3.8-flash`, which spends far more output
tokens on reasoning.

### Region — where requests actually go

`vertex.location` is set to `global`. **`global` is a routing endpoint, not a place.**
Google picks a region with capacity and discloses nothing about which: the response
carries no region header and no region field, and the hostname resolves to anycast
front-ends that say nothing about where inference ran.

`gemini-3.1-flash-lite` is served from **no regional endpoint at all** — 17 regions
probed on 2026-10-04, every one 404 (asia-south2 returns 501; Vertex is not available
there). The `global` endpoint is the only way to reach this model.

| Need | Option today |
| --- | --- |
| Gemini 3.x | `global` only, no region pinning, no residency guarantee |
| Inference pinned to India | `gemini-2.5-flash` on `asia-south1` — the only Gemini serving that region |

So region and model generation are currently a trade: **you can have Gemini 3.x, or
you can have Indian data residency, not both.**

**Decided 2026-10-04: stay on `global` with `gemini-3.1-flash-lite`.** Residency is a
preference rather than a compliance requirement here, and the digest processes public
news articles, not customer data. Revisit only if that changes.

There is still no automatic fallback between regions — silently rerouting an
India-pinned workload to `global` would defeat the reason for pinning it, so a run
against an unavailable region stops and says so.

```bash
uv run ai-radar probe                                           # scoring model
uv run ai-radar probe --model gemini-2.5-flash                  # any model
uv run ai-radar probe --locations asia-south1,asia-southeast1   # any regions
```

Re-run `probe` periodically and switch `vertex.location` the day a 3.x model reaches
a region you want.

### Quota exhaustion is not a transient error

A per-day quota wall arrives as a 429 exactly like a burst limit, but backoff never
clears it — the free tier returns a `retryDelay` of nine hours. Retrying spends
minutes to produce nothing, so `QuotaExhausted` is raised immediately and names the
fix.

### Pacing, and what it costs in wall-clock

Calls are issued **one at a time**, `limits.request_spacing_seconds` apart (currently
**90s**). Parallelism bought nothing for a once-a-day batch and cost 26 of 30 items to
rate limiting when it was tried.

Spacing is the dominant term in how long a run takes — `candidates × spacing` — so it
decides when the trigger must fire:

| Candidates | Pacing | Start by, to deliver 07:00 |
| --- | --- | --- |
| 20 | ~28 min | 06:21 |
| 40 | ~58 min | 05:51 |
| 60 | ~88 min | 05:21 |

`ai-radar run --estimate` prints this for the current settings; `digest.start_at` in
`profile.yaml` records the answer for the scheduler. Recompute whenever spacing or the
candidate cap changes — this is the easiest thing in the system to change in one place
and forget in the other.

Note the laptop must stay awake for that whole window, which is a stronger constraint
at 90 minutes than it was at four. Trimming the candidate cap is the cheapest lever if
that becomes awkward.

## The digest runs all seven days

No source may be empty by design. arXiv's RSS carries only that day's
announcements and declares `skipDays` for Saturday and Sunday, so arXiv is read
through its **API** instead, sorted by submission date — that returns the newest
papers whatever the day. `validate` treats an empty feed as a fault, and a test
asserts no source reintroduces arXiv RSS or a skip-day flag.

Because arXiv supplies the bulk of the candidates, `ingest` interleaves sources
round-robin rather than concatenating them. Without that, the `--limit` cut would
fall entirely inside arXiv and never reach the news feeds.

## Feeds without RSS

Deepgram, AssemblyAI, ElevenLabs, Cartesia, LiveKit, Speechmatics and Sarvam
publish no RSS at the usual paths — checked 2026-10-04. They are the vendors
closest to the beat, so reaching them needs HTML scraping or a search API
(Exa/Tavily). Scheduled for a later phase, tracked in `sources.yaml`.

## Develop

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src/ai_radar
```
