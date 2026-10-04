# Session status

**Updated:** 2026-10-04
**Phase:** 0 complete, plus early parts of 1–3. Entering the manual evaluation period.

## Where we left off

The pipeline runs end to end against live feeds with live scoring, persists every run,
and learns from votes. Verified working, not just written.

| Thing | State |
| --- | --- |
| Ingest → dedupe → score → select → Markdown | working |
| 10 sources | all returning items daily |
| Scoring via Vertex AI Gemini 3.1 Flash Lite | working, ~$0.0004/story |
| SQLite persistence | working, 5 runs / 29 stories / 2 votes recorded |
| Dashboard with voting | working at `http://127.0.0.1:8000` |
| Feedback → scoring prompt | **verified**: one down-vote moved a story 52.0 → 0.0 |
| Cross-run dedupe | working, skips anything previously judged |
| Tests / ruff / mypy strict | 18 passing, all clean |

**Nothing is committed.** The repo has zero commits. This is the oldest open item.

## Current milestone

**Run it manually every day for 1–2 weeks and judge whether the ranking is any good.**

Daily:

```bash
caffeinate -i uv run ai-radar run --limit 40   # ~60 min, keeps the Mac awake
uv run ai-radar serve                          # read, then vote on every story
```

`caffeinate -i` is not optional — a run is only written at the end, so a sleeping Mac
loses the whole thing.

**What to watch:** the dashboard's *kept, last 7* against *kept, all runs*. If the
recent figure climbs, the ranker is learning. If it has not moved after a week of
voting, the rubric weights in `config/profile.yaml` are wrong — more votes will not
fix that.

Vote on everything delivered, including stories you feel neutral about. Unvoted
stories teach nothing and are excluded from the precision figure.

## Open items, roughly in order

1. **Commit and push.** Zero commits today; after two weeks of runs there will be a
   `data/ai_radar.db` worth not losing. Branch off `main` rather than committing to it.
2. **Promote the model eval into the repo.** It lives in a scratchpad and is the only
   way to tune cluster weights without flying blind. 9 hand-labelled stories plus a
   comparison harness.
3. **Watch for over-correction from votes.** A single down-vote currently zeroes a
   story. Expected to moderate as votes accumulate; if it does not, reduce `per_side`
   in `feedback.learned_examples`.
4. **Telegram delivery with inline vote buttons** — Phase 1 remainder. Needs long
   polling locally, a webhook after deploy.
5. **Vendors with no RSS** (Deepgram, AssemblyAI, ElevenLabs, Cartesia, LiveKit,
   Speechmatics, Sarvam) — closest sources to the beat, currently unreachable. Needs
   scraping or a search API.
6. **Deploy** (Hetzner or Railway) — only after the trial says the digest is worth
   receiving.

## Known rough edges

- Summarization is not implemented. The digest currently shows the scorer's rationale,
  which is one sentence. `models.summarizing` is configured but unused.
- A full 60-candidate run paces for ~88 minutes. 40 is the practical cap.
- The dashboard has no auth. Fine on localhost, not fine once deployed.
