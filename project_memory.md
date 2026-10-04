# Project memory

Durable context for AI Radar. Decisions and measured facts, with the reasoning that
produced them. **Treat entries here as settled.** Re-open one only by arguing against
the reason recorded, not by overlooking it.

Full architecture: [AI Radar — Architecture & Implementation Plan](https://claude.ai/code/artifact/c586d172-ae74-4d5c-aa74-1db9590ed85f)

## What this is

A daily digest of voice-AI news, ranked against one reader's interest profile,
delivered at 07:00 IST. The reader builds **voice AI agents for enterprise customer
support in telecom** and tracks voice agent platforms, realtime and speech-to-speech
models, TTS realism, STT accuracy, provider benchmarks, and Indic-language speech.

Five stories a day to start.

## Decisions

### Architecture

**It is a deterministic pipeline, not an autonomous agent.** Ten stages, each a pure
function plus a database write. An LLM judges relevance and writes prose; nothing else
is model-driven. A free-roaming agent loop would be non-deterministic, 5–20× the cost,
undebuggable at 06:58, and impossible to A/B test.

**Weights are applied in code, not by the model.** The model returns five subscores;
`Score.weighted_total` combines them using the rubric in `profile.yaml`. Retuning
weights needs no prompt change and no re-run.

**Clusters are defined by capability, never by company.** A release from an unknown lab
must score the same as an incumbent's. A vendor-name keyword list silently becomes a
watchlist and ranks familiar press releases above the thing that matters from an
unfamiliar source. A test asserts no vendor name reaches the scoring prompt.

### Models and providers

**No vendor LLM SDK.** All calls go through `llm.py` → LiteLLM, so switching provider
is a string in `profile.yaml`. The cost: structured output and prompt caching are no
longer handled for us, and need a thin per-provider adapter.

**Gemini 3.1 Flash Lite on Vertex AI, location `global`.** Chosen on measurement, not
preference — see Measured facts.

**Deepgram is not an LLM provider.** The 40-char hex key in `.env` is a valid Deepgram
key (speech: STT/TTS). It cannot serve GPT or any chat model; `api.openai.com` returns
401 for it and Deepgram exposes no OpenAI-compatible chat endpoint. It is kept as
`DEEPGRAM_API_KEY` and is unused by the pipeline.

### Operations

**Calls are serial, 90 seconds apart.** Parallelism bought nothing for a once-a-day
batch and cost 26 of 30 items to rate limiting. Consequence: runtime is
`candidates × spacing`, so 07:00 is a *finish* time. 40 candidates ≈ 58 min → start
05:51. Recorded as `digest.start_at`.

**No automatic region fallback.** A region is chosen for residency or latency; silently
rerouting would defeat that. A run against an unavailable region stops and says so.

**Quota exhaustion is never retried.** A per-day quota wall arrives as the same 429 as a
burst limit but returns a nine-hour retry delay. `QuotaExhausted` raises at once;
transient capacity errors retry with exponential backoff.

**Each story is judged once, ever.** Cross-run dedupe skips anything previously scored,
not merely anything delivered — a story rejected yesterday should not return asking for
the same verdict, and re-scoring costs 90 seconds each.

**Laptop first.** Two to four weeks of manual daily runs before any server exists. The
only thing a laptop cannot do is wake at 07:00, which is the sole reason to deploy.

### The feedback loop

Votes are replayed into the scoring prompt as worked examples. No fine-tuning, no
embedding store — so you can read exactly what the model was told, and deleting a vote
undoes its influence. This is the only thing that makes the ranker personal.

Residency in India is a **preference, not a compliance requirement** — the digest
processes public news, not customer data. This is why `global` is acceptable.

## Measured facts

All measured 2026-10-04 unless noted. Re-measure rather than trusting these indefinitely.

### Model comparison — 9 hand-labelled stories, 5 on-beat, 4 to reject

| Model | Separation | Inversions | $/item | Median latency |
| --- | --- | --- | --- | --- |
| **gemini-3.1-flash-lite** | **86.5** | 0 | **$0.000387** | 1.8s |
| gemini-3.5-flash-lite | 85.8 | 0 | $0.000502 | 1.2s |
| gemini-3.1-pro-preview | 85.1 | 0 | $0.011464 | 6.9s |
| gemini-3-flash-preview | 82.8 | 0 | $0.002014 | 36.4s |
| gemini-3.8-flash | 80.9 | 0 | $0.002071 | 3.6s |

**Zero inversions on every model** — the task is within all of them, so the expensive
options buy nothing. Flash Lite separated best *and* cost least; Pro separated worse at
30× the price. Caveat: 9 items is a small set and the separation gaps are within noise.
What is decisive is the cost spread and the 36s latency outlier.

Running cost: **~$0.0004/story → ~$0.75/month.**

### Routes to Gemini

| Route | Price | Verdict |
| --- | --- | --- |
| `vertex_ai/...` (ADC) | identical | **In use.** Real capacity. |
| `gemini/...` (API key) | identical | Free tier = **20 requests/day/model**. A third of one run. |

Per-token pricing is the same on both; the 0.8% gap measured was token-count variance.
The choice is about quota and region control, not price.

### Regions

`gemini-3.1-flash-lite` answers from **no regional endpoint at all** — 17 probed, all
404. `global` is the only route. `global` is a routing endpoint, not a place: Google
picks a region and does not disclose which (no region header, no region field).

`asia-south1` is enabled for the project but serves **only Gemini 2.5**. So: Gemini 3.x
*or* Indian residency, not both. Re-check with `ai-radar probe`.

### Sources

Ten feeds, all returning items daily. arXiv is read through its **API, not RSS** —
arXiv RSS declares `skipDays` for weekends and is empty then, and the digest runs all
seven days. The three arXiv categories supply ~75% of candidates, so ingest interleaves
sources round-robin; concatenated, a candidate cap would fall entirely inside arXiv.

**No RSS at any conventional path:** Deepgram, AssemblyAI, ElevenLabs, Cartesia,
LiveKit, Speechmatics, Sarvam. These are the vendors closest to the beat. Reaching them
needs scraping or a search API.

### Feedback loop, verified

One down-vote moved a story from a stable 50–54 across three runs to **0.0** on the
next; the up-voted story held at 85.0. The loop demonstrably closes. It is currently
*very* responsive — watch for over-correction while vote counts are low.

## Gotchas learned the hard way

- **`ruff format` reflows code, which silently breaks string-match patches.** Three
  no-op patches happened this way, caught only by a test or a live run. Assert that a
  replacement applied.
- **`grep -c` counts lines, not matches.** Single-line XML feeds report 0 items.
- **A feed returning 200 with no items may be fine or broken** — arXiv RSS at weekends
  versus a dead URL. The distinction matters.
- **uvicorn without `--reload` does not pick up code changes, but Jinja re-reads
  templates from disk.** A new template can meet an old route. Restart after editing
  `app.py`.
- **SQLAlchemy instances detach when the session closes.** Read ids inside the block.
- **Gemini 3 spends output budget on thinking before emitting JSON**, so a low
  `max_tokens` truncates mid-object and surfaces as a parser error. Detect
  `finish_reason == "length"` explicitly.
