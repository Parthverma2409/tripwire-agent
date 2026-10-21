# Tripwire — AI Booking-Lifecycle Sync Assistant

A booking's lifecycle — not just its creation — should always be reflected in your
calendar, without you having to remember to update it.

**Part A** is a voice- and email-driven agent that keeps a calendar consistent with the
true state of travel bookings across creation, rescheduling and cancellation.
**Part B** is a self-hosted platform that captures, scores and compares that agent's
behaviour, and the two research studies built on it.
**Tripwire** is the app that puts both in front of a person: a local web UI that reads
booking mail from Gmail or IMAP, keeps Google Calendar in step, and asks before it
deletes or moves anything (in the browser, or on Telegram).

### Run the app

**Windows:** double-click `run_app.bat`. **macOS / Linux:** `./run_app.sh`.
It opens `http://127.0.0.1:8765` in **demo mode** (sample emails, practice calendar).
To connect your real accounts, follow **[docs/CONNECT.md](docs/CONNECT.md)**.

```bash
python -m pip install -e ".[app]" && (cd web && npm install && npm run build)
python -m packages.app                          # the app, on 127.0.0.1:8765
python -m packages.nlp check-email my.eml       # what the extractor reads from a real email
```

### Run the research code

Everything below runs locally against in-memory fakes, so the whole suite is deterministic and
free. No API keys, no models and no network are needed to run any command below.

```bash
git clone <this-repo> && cd booking-sync
python -m venv .venv
./.venv/Scripts/Activate.ps1            # PowerShell;  source .venv/bin/activate on POSIX
python -m pip install -e ".[dev]"

python -m pytest tests/ -q              # unit + API tests (browser tests skip unless set up)
python -m packages.demo                 # narrated end-to-end walkthrough
```

The demo is the fastest way to understand what this does. Start there.

---

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Frozen contracts: trajectory schema, provider protocols | done |
| 1 | Domain core, reconciliation, FR5 safety gate, 66-scenario suite | done |
| 2 | NLP layer: intent/slot, email extraction, metrics, baseline | done (LLM path needs a model) |
| 3 | End-to-end agent: orchestrator, email watcher, date resolution | done |
| 4 | Capture SDK (FR7), ingest pipeline, evaluator registry (FR8) | done |
| 5 | Google adapters, OAuth + token store, voice shell, activation window | done (unverified vs live Google) |
| 6 | Dashboard (FR9), model comparison (FR10), RQ1/RQ2 | done (awaiting human labels) |
| 7 | Tripwire app: FastAPI service, Next.js UI, staged email extraction (schema.org, .ics, vendor profiles), sender trust, IMAP, Telegram approvals, Ollama fallback, local Whisper | done (unverified vs live services) |

**797 tests, 66 scenarios, 0 FR5 violations.** What is *not* done is measurement, not
code: see [Honest limits](#honest-limits).

---

## Commands

```bash
# Safety and correctness
python -m packages.bench --all                  # 66 scenarios + the FR5 build gate
python -m packages.bench --all --dump-trajectories data/trajectories

# NLP
python -m packages.nlp build                    # generate + leakage-checked splits
python -m packages.nlp evaluate --split test    # held-out scores

# Evaluation and research
python -m packages.bench.latency                # NFR1 p50/p95
python -m packages.bench.compare                # FR10 sweep
python -m packages.bench.recommend              # FR10 gate
python -m packages.analysis.rq1                 # reasoning as an observability signal
python -m packages.analysis.rq2                 # judge reliability + bias checks

# Dashboard (FR9)
python dashboard/build_index.py                 # then open dashboard/index.html

# The app
python -m packages.app                          # web UI + API on 127.0.0.1:8765
python -m packages.nlp check-email path.eml     # per-stage extraction report, touches nothing
```

`packages.bench` exits **2** on any unconfirmed destructive action. Wire that into CI:
FR5 is a build gate, not a metric.

---

## Two design rules

**1. Reconciliation is a pure function.** `decide(events, booking) -> Decision` makes no
network call, reads no clock and runs no model. Execution lives separately in
`ReconciliationEngine.apply`. That is why the scenario suite runs in under a second and
can be replayed across five candidate models for FR10 at no cost.

**2. The FR5 gate is structural, not a policy check.** `apply_destructive()` accepts only
a `ConfirmedDecision`, and the only way to construct one is `ConfirmationRequest.confirm()`.
Forgetting a flag is a bug that happens; here there is no code path that deletes an event
without recorded consent — including for a future contributor who has never read this file.

## How this codebase is checked

Three habits, because a green suite is not by itself evidence:

- **Negative controls.** `tests/test_negative_control.py` bypasses the FR5 gate to prove
  the detector fires. A safety check that has never failed proves nothing.
- **Mutation testing.** Six plausible bugs injected into the reconciliation engine; all
  six caught, none survived. Disabling the FR5 gate alone fails 39 scenarios. Re-run it
  after changing `matching.py` or `reconciliation.py`; method and results in
  `data/scenarios/README.md`.
- **Structural audit as tests.** `tests/test_repo_consistency.py` checks methods called on
  `self` that do not exist, unused imports, scenario-ID hygiene, both trajectory producers
  agreeing with the frozen schema, and every documented command being real. It exists
  because an audit found a method called but never defined — every FR6 query crashed while
  282 behavioural tests passed.

---

## Requirement traceability

| Req | Implemented in | Verified by |
|---|---|---|
| FR1 spoken create/reschedule/cancel | `packages/sync_agent/agent.py` | `tests/test_agent_end_to_end.py` |
| FR2 watch inbox, parse booking mail | `packages/nlp/email_extract.py`, `email_watcher.py` | `tests/test_email_extract.py` |
| FR3 match booking against calendar | `packages/core/matching.py` | `tests/test_matching.py`, families `new_booking`, `ambiguous_match` |
| FR4 create/update/delete event + alarm | `packages/sync_agent/reconciliation.py` | families `new_booking`, `reschedule`, `cancellation` |
| **FR5 confirm before destructive change** | `packages/core/confirmation.py`, `decisions.py` | `tests/test_safety_gate.py`, `tests/test_negative_control.py`, family `safety` |
| FR6 answer booking-status queries | `SyncAgent._answer_query` | `tests/test_agent_query.py` |
| FR7 async trajectory capture | `packages/capture_sdk/` | `tests/test_capture_sdk.py` (latency measured with ingest dead) |
| FR8 score via evaluator registry | `packages/eval_ingest/` | `tests/test_evaluators.py`, `tests/test_eval_ingest.py` |
| FR9 dashboard + drill-down | `dashboard/` | `tests/test_dashboard.py` |
| FR10 comparison + gated recommendation | `packages/bench/compare.py`, `recommend.py` | `tests/test_recommend.py` |
| NFR1 latency p50/p95 | `packages/bench/latency.py` | measured: p50 ~0.21 ms, p95 ~0.50 ms (in-process floor) |
| NFR2 idempotent reconciliation | `decide()` duplicate paths | family `duplicate_notification` |
| NFR3 OAuth2 least privilege, tokens encrypted | `packages/providers/oauth.py`, `tokens.py` | `tests/test_google_providers.py` (tamper detection) |
| NFR4 activation window | `packages/sync_agent/activation.py` | `tests/test_activation_window.py` (incl. midnight-crossing) |
| NFR5 ingest throughput | `packages/eval_ingest/pipeline.py` | measured: ~3,400/s, far past 1,000/day |
| NFR6 independent deployability | protocols in `packages/providers/` | fakes and real adapters share one interface |
| RQ1 / RQ2 | `packages/analysis/rq1.py`, `rq2.py` | `tests/test_rq_analysis.py`, `tests/test_statistics.py` |
| FR2+ staged extraction: schema.org, .ics, vendor, rules, LLM | `packages/nlp/structured.py`, `ics.py`, `vendors.py`, `llm_email.py` | `tests/test_email_pipeline.py` |
| Sender authentication shown at the FR5 prompt | `packages/nlp/trust.py` | `tests/test_email_pipeline.py` (forged cancellation) |
| App API, local-only guards, OAuth state | `packages/app/` | `tests/test_app_api.py`, `tests/e2e/test_ui.py` |

---

## Layout

```
packages/core/          domain model, matching, decisions, confirmation gate  (pure)
packages/providers/     CalendarProvider / MailProvider ports, fakes, Google adapters
packages/sync_agent/    SyncAgent orchestrator, reconciliation, email watcher
packages/nlp/           intent/slot extraction, email extraction, resolution, metrics
packages/capture_sdk/   FR7 async capture, never blocks the agent
packages/eval_ingest/   FR8 normalise, validate, quarantine, evaluator registry
packages/bench/         scenario replay, latency, FR10 comparison + gate
packages/analysis/      pure-Python statistics, RQ1 and RQ2
packages/demo/          narrated walkthrough
packages/app/           Tripwire: FastAPI service, runtime, scheduler, OAuth flow, SQLite state
packages/notify/        Telegram approvals (signed buttons, long polling)
web/                    Next.js UI, exported static and served by packages/app
dashboard/              FR9 static offline dashboard (no build step), also at /research/
data/vendors/           sender profiles for the email extractor (MakeMyTrip, IndiGo, IRCTC, Uber...)
data/scenarios/         66 reconciliation scenarios -- the test suite and FR10 benchmark
schemas/                frozen trajectory contract
```

`data/scenarios/` is source, not data: it is the regression suite, the FR5 build gate and
the FR10 benchmark at once. Everything else under `data/` is generated or supplied by you.

---

## Data

**This repository ships no datasets.** Generated artefacts are git-ignored and rebuilt on
demand; the test suite generates its own inputs, so `pytest` works on a fresh clone with
no setup step.

To load real corpora, real email, human labels or live Google credentials, see
**[DATASETS.md](DATASETS.md)**. It covers the licence trap in ATIS, the leakage key that
silently disables the split protection if you set it wrong, and the labelling protocol
that has to happen before the labelling session rather than after.

---

## Honest limits

Worth knowing before quoting any number from this repository.

- **The Google adapters, Gmail attachment fetch, IMAP, Telegram, Ollama and Whisper have
  never run against the real services.** No credentials or models existed on the build
  machine; `docs/CONNECT.md` is how they get wired and the Connections page tests each one.
  The Google adapters' tests prove self-consistency and correct handling of Google's
  documented error dialect; they cannot prove the payload shapes are right. `DATASETS.md`
  lists exactly what stays unverified until you run it.
- **Vendor profiles (`data/vendors/vendors.yaml`) were written from public knowledge, not
  from real mail.** The email fixtures under `data/emails/fixtures/vendors/` are synthetic.
  Run `check-email` on your own booking mail before trusting a profile.
- **Token encryption.** The app writes the v2 format (AES-256-GCM via `cryptography`, key
  in the OS keyring). The v1 stdlib construction remains the store's default for the
  research code and is read for migration; it is not vetted crypto.
- **NLU scores come from template-generated data and are an optimistic ceiling.** Real
  speech is messier than any template set.
- **RQ1 and RQ2 arithmetic is real; the labels are not.** No human labels have been
  collected. The RQ1 AUC difference interval currently contains zero, so the honest
  reading is "no detectable difference on this fault mix". Self-preference is explicitly
  *not measured* — an earlier version returned a confident figure that would have been
  fabricated. Every LLM-judge property awaits a local model.
- **The scenario suite contains no genuine failures**, by design. That means the D5
  "clear failure" stratum cannot be filled from observed behaviour — decide how you will
  fill it before sampling.
- **NFR1 latency is an in-process floor**, measured against fakes with a rule-based
  extractor. No ASR, TTS, LLM or network is in that path.

---

## Requirements documents

The SRS, use-case specifications and StarUML diagrams live in `docs/` but are
**git-ignored** — they are coursework submissions rather than part of the software.
`docs/diagram-corrections.md` is tracked and lists the defects found while implementing
the class diagram, including two that changed the code.
