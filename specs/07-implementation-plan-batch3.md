# Implementation Plan — Batch 3

Selected features: ONB-1, SPK-1, NOT-1, BRF-1, PIP-1, DSK-1, HLT-1, ANA-1, REC-1, EXP-1

Scope tag: all features in this batch stay **inside the single-user, local-first MVP**. Nothing here introduces multi-user, cloud, or auto-email flows.

Related documents:
- `docs/SECURITY.md` — contains six **proposed** security enhancements (P-1 … P-6) deliberately **not** scheduled for implementation in this batch.
- `specs/05-future-roadmap.md` — existing roadmap; items here replace prior ad-hoc notes on speaker enrollment (N2), analytics (A1), recurring meetings (N9) and health check (R5) with more concrete designs.

## Shipped Status — Batch 3 Complete ✅

All 10 features across 4 phases live on main.

| Phase | Feature(s) | Status | PR |
|---|---|---|---|
| 0 | PIP-1 | **Shipped** | [#3](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/3) |
| 1 | HLT-1 · DSK-1 · ONB-1 | **Shipped** | [#4](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/4) |
| 2 | SPK-1 | **Shipped** | [#5](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/5) |
| 3 | REC-1 · ANA-1 | **Shipped** | [#6](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/6) |
| 4 | BRF-1 · NOT-1 · EXP-1 | **Shipped** | [#8](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/8) |
| 5 | Docs sweep | **Shipped** | [#10+](https://github.com/PietKonijn007/MeetingMinutesTaker/pulls) |

Each feature section below has a **Status** line and, where shipped, a short implementation-notes block covering deviations from the original spec.

---

## Feature Specifications

### ONB-1: First-Run Onboarding Wizard (`mm doctor`)

**Status:** Shipped in [PR #4](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/4). Implementation notes: 10 checks shipped as specified; no-op-on-non-macOS gracefully handled for check #3 (BlackHole); `mm doctor --json` added for the `/onboarding` page. PR #8 added check #11 (WeasyPrint natives) at the tail of the existing check list. Ollama LLM probe hits `/api/version` rather than a completion (avoids spurious fails when no model is loaded).

**Problem.** Setup has three independent gates — Anthropic API key, HuggingFace token, BlackHole aggregate device — each with its own failure mode. Errors currently surface only when the user tries to record, far from the root cause.

**Description.** A single diagnostic and guided-fix surface, available as:
- CLI: `mm doctor` — runs all checks, prints a table, exits non-zero on any failure.
- Web: auto-opens at `/onboarding` when `mm serve` starts with an empty database, and is reachable any time from Settings → "Diagnostics".

**Checks (in order, each with `ok | warn | fail` status + actionable fix hint):**

| # | Check | Failure hint |
|---|---|---|
| 1 | Python ≥ 3.11 | "Install Python 3.11+ and recreate the venv" |
| 2 | `ffmpeg` on PATH | "Run: brew install ffmpeg" |
| 3 | BlackHole aggregate device present (macOS) | "Re-run install.sh to configure Meeting Capture device" |
| 4 | HuggingFace token set and model license accepted | "Set HF_TOKEN env var; accept pyannote license at <link>" |
| 5 | LLM provider reachable (dry-run 1-token completion) | "Check API key / provider URL in Settings" |
| 6 | Database exists and `PRAGMA integrity_check` = ok | "Run: mm repair (see HLT-1)" |
| 7 | Free disk space vs retention settings | "See DSK-1 suggestions" |
| 8 | GPU detected (MPS/CUDA/ROCm) or explicit CPU fallback acknowledged | "Review Hardware section in Settings" |
| 9 | Whisper model files present | "Click Download (see R6) or run: mm embed --warm" |
| 10 | `sqlite-vec` loadable | "Rebuild with: pip install -e ." |

**Technical approach.**
- New module `src/meeting_minutes/doctor.py` exposes `run_checks() -> list[CheckResult]`.
- Each check is a pure function returning `{name, status, detail, fix_hint}`.
- CLI prints via `rich.table`. API endpoint `GET /api/doctor` returns the JSON list; `/onboarding` page renders it with a retry button per failed check.
- Empty-DB detection: existing `mm init` logic already knows this state; `mm serve` just checks row count of `meetings` and redirects to `/onboarding` on first navigation.

**Effort.** Medium (4–6 h).

**Acceptance.**
- `mm doctor` exits 0 on a healthy system, non-zero with a readable table otherwise.
- First-run browser experience: empty DB + `mm serve` → auto-route to `/onboarding`, each check has a green check or a clickable fix hint.
- Every failure path leads to a one-line command the user can copy-paste.

---

### SPK-1: Passive Speaker Centroid Learning (Modified from prior N2 voice enrollment)

**Status:** Shipped in [PR #5](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/5). Implementation notes: pyannote 4.0.4's `SpeakerDiarization.apply()` exposes `speaker_embeddings` aligned with `labels()` — no separate `Inference` run needed. `person_id` is `String` (UUID) across the codebase, not `Integer` as the schema in this spec shows. Backfilling centroids from pre-SPK-1 meetings still requires `mm rediarize` + click-save-in-UI (a proper `mm spk1 backfill` command is a documented follow-up).

**Change from earlier roadmap.** Replaces the 30-second explicit enrollment flow in prior N2. No dedicated recording step: centroids are **built from real meeting audio** and **improve after every meeting**. This produces better acoustic match (same mics, codecs, rooms) and zero setup friction.

**Problem.** Every meeting today requires manual relabeling of `SPEAKER_00 → Jon`. Diarization already produces speaker embeddings internally; they are currently discarded after use.

**Flow.**
1. **First meeting for a person:** user maps a pyannote cluster to a `persons` row. The embedding for that cluster is persisted as the person's first sample.
2. **Subsequent meetings:** for each cluster, cosine-similarity is computed against every stored centroid.
   - `> 0.85` → auto-label, user sees the name pre-filled with a "suggested" badge.
   - `0.70 – 0.85` → suggestion with "?" badge; one-click confirm or reject.
   - `< 0.70` → unlabeled, falls back to current manual flow; user can pick an existing person or create a new one.
3. **Confirmation writes a new sample.** Centroid is recomputed as the mean of confirmed samples (cheap at this scale).
4. **Correction rewrites provenance.** If the user relabels a cluster, samples previously added under the wrong person are flipped to `confirmed=false` so the centroid is recomputed cleanly.

**Data model.** New table (Alembic migration required):

```sql
person_voice_samples (
  sample_id      INTEGER PRIMARY KEY,
  person_id      INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
  meeting_id     TEXT    NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
  cluster_id     TEXT    NOT NULL,          -- pyannote's SPEAKER_XX label in that meeting
  embedding      BLOB    NOT NULL,          -- float32, dim depends on pyannote model
  embedding_dim  INTEGER NOT NULL,
  confirmed      BOOLEAN NOT NULL DEFAULT 0,
  created_at     TIMESTAMP NOT NULL,
  UNIQUE (meeting_id, cluster_id, person_id)
)
CREATE INDEX idx_voice_samples_person ON person_voice_samples(person_id, confirmed);
```

**Technical approach.**
- Extend `DiarizationEngine` to expose per-cluster embeddings (pyannote's pipeline already computes them; we need to surface them on the output object).
- New module `src/meeting_minutes/system1/speaker_identity.py`:
  - `extract_cluster_embeddings(diarization_output) -> dict[cluster_id, np.ndarray]`
  - `match_clusters(embeddings, candidates: list[PersonCentroid]) -> list[Match]` (cosine similarity, returns score per candidate)
  - `update_centroid_on_confirm(person_id, meeting_id, cluster_id)` — writes sample row with `confirmed=true`.
  - `invalidate_on_correction(...)` — flips prior sample to `confirmed=false`.
- Pipeline wiring: after diarization, call `match_clusters` against all persons with ≥ 1 confirmed sample; write pre-filled name suggestions into the speaker-rename UI payload.
- Cap centroid computation at the **20 most recent confirmed samples** per person (FIFO) to prevent drift as a voice changes over time.

**Gotchas handled in spec.**
- **Cold start:** first ~3 meetings still need manual mapping; the `/onboarding` page (ONB-1) explains this expectation.
- **Unknown speaker with substantial speech:** if no centroid matches but the cluster has >30 s of speech, prompt "Create new person?" instead of forcing a match.
- **Minimum speech threshold:** clusters with < 5 s of speech are not written as samples (too noisy to be useful).
- **Privacy:** embeddings are low-dimensional vectors; they do not reconstruct audio. Still, they are user data — covered by existing retention and encryption flags once encryption is enabled.

**Effort.** Medium–High (6–8 h).

**Acceptance.**
- After labeling Jon in 3 distinct meetings, the 4th meeting's Jon cluster is auto-suggested with score > 0.85.
- Relabeling Jon → Sarah on an already-processed meeting removes that sample from Jon's centroid on the next recompute.
- Speakers with < 5 s of speech produce no sample row.
- A brand-new person created in the speaker-rename UI produces their first voice sample.

---

### NOT-1: Desktop Notifications on Pipeline Events

**Status:** Shipped in [PR #8]. Click URL uses the plain `http://localhost:8080/meeting/{id}` form; the `mm://` URL handler is deferred (it requires an `install.sh` Info.plist registration step that's disproportionate for the win). `pync` import is deferred and platform-gated; missing dep logs once at INFO and becomes a no-op.

**Problem.** Pipeline takes ~2 minutes; users cannot tell when it's done without watching the browser tab.

**Description.** macOS desktop notifications fire on pipeline state transitions:
- `complete` — notification title "Meeting ready: {title}", body shows duration + action-item count.
- `failed` — notification title "Pipeline failed: {title}", body shows the error code from the error taxonomy (future work) or short error string for now.

Clicking the notification opens the web UI to the meeting (via a `mm://meeting/{id}` URL handler registered by `install.sh`; fallback to `http://localhost:8080/meeting/{id}` if the handler isn't registered).

**Technical approach.**
- Dependency: `pync` (macOS only). Wrap in a thin abstraction `src/meeting_minutes/notifications.py` with a no-op implementation for Linux/Windows so the main pipeline code doesn't branch on platform.
- Hook into the existing pipeline stage transitions; fires on `complete` and `failed` only.
- Config: `notifications.enabled: bool` (default `true` on macOS, `false` elsewhere), `notifications.sound: bool`.
- `mm://` URL handler: registered via a small `Info.plist` fragment written by `install.sh` during Launch Agent setup.

**Effort.** Small (2 h).

**Acceptance.**
- Stopping a recording and letting the pipeline finish produces a macOS notification.
- Clicking the notification opens the meeting page.
- Disabling `notifications.enabled` stops them from firing without errors.

---

### BRF-1: Pre-Meeting Briefing Page (with inline record start)

**Status:** Shipped in [PR #8]. All six sections are pure-query; the LLM summary path is gated behind `brief.summarize_with_llm` (default false). Context-excerpts reuse the existing embedding engine and fall back to newest summary/discussion chunks if semantic search is unavailable. Deep-links wired from `/people/:id` and `/series/:id`.

**Modification from initial proposal.** The briefing page is **also** the launch point for the next meeting — the user can type speaker names, load context, and hit **Start recording** without leaving the page. This merges the current `/record` flow into `/brief` for recurring-partner meetings.

**Problem.** The archive today is reactive — look up past meetings after the fact. There is no surface that combines "what did Jon promise / say / feel like" with "start recording the next meeting with Jon now."

**Description.** New page `/brief` accepts either:
- A **person** (or set of people) — pulls their recent meetings, open actions, unresolved parking-lot items, recent sentiment.
- A **meeting type + expected attendees** — templates a typical briefing (e.g., standup → prior blockers; 1:1 → last meeting's commitments + career notes).

**Rendered sections (top to bottom):**
1. **Who & when last:** attendee cards, last meeting date, cadence (from REC-1 if available).
2. **Open commitments:** action items assigned to attendees, overdue flagged red.
3. **Unresolved topics:** last N `parking_lot` entries involving the attendees, newest first.
4. **Recent sentiment:** micro-trend chart (sparkline) over the last 5 meetings with those attendees, per person.
5. **Recent decisions:** decisions involving these attendees, newest first, with one-sentence rationale.
6. **Context excerpts:** RAG query result — top-3 most relevant transcript chunks from the last 90 days.
7. **Start recording panel (pinned bottom):**
   - Pre-filled title (editable)
   - Meeting type dropdown (pre-picked from attendee pattern)
   - Attendee list pre-filled with centroid-suggested speaker names (from SPK-1)
   - Live note-taking textarea (pre-seeded with "Carry-forward" bullet list of open actions)
   - **Start recording** button — calls the existing `POST /api/recording/start` with the pre-filled payload, then navigates to `/record` for the live waveform view.

**Technical approach.**
- New route `/brief` in SvelteKit; query params `?person=<id>&person=<id>&type=<type>`.
- New API `GET /api/brief?people=<ids>&type=<type>` returns a `BriefingPayload` Pydantic model aggregating the six data sections.
- **No new LLM call in the default path** — all six sections are pure queries against existing data. Section 6 (context excerpts) reuses the existing `ChatEngine.retrieve_chunks()` without the synthesis step, so no tokens are spent.
- Optional LLM summarization (one 2-sentence synthesis at the top) behind a toggle `brief.summarize_with_llm: bool` (default off; user can flip on in Settings).
- Recording start panel is a thin wrapper over existing recording controls — no new endpoint.

**Effort.** Medium (5–7 h).

**Acceptance.**
- `/brief?person=<jon-id>` renders all six sections in under 500 ms (pure DB work).
- The "Start recording" panel pre-fills title, meeting type, and attendees; on click, recording begins and the UI transitions to `/record`.
- The carry-forward note block includes Jon's open action items as bullets.
- With zero past meetings for Jon, the page renders gracefully with empty-state copy ("No prior meetings yet").

---

### PIP-1: Resumable Pipeline with Checkpoints

**Status:** Shipped in [PR #3](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/3). Implementation notes: `PipelineStageORM` lives in `system3/db.py` (where all SQLAlchemy ORM lives), not `models.py` (Pydantic only). `resume_from` treats `CAPTURE` as a no-op inside resume (audio can't be programmatically re-recorded); `TRANSCRIBE` + `DIARIZE` re-run together since they share one pass. `POST /api/meetings/:id/resume` uses a simple background task with a `job_ref` string — no separate job table.

**Problem.** Pipeline is implicitly resumable today (artifacts live on disk), but the state machine is not explicit. If generation fails twice, the user has to know the right incantation (`mm generate` vs `mm reprocess --from=<stage>`). Crash during stage N leaves no record that stage N was interrupted.

**Description.** Persist per-(meeting, stage) state and expose a clear resume path.

**State machine stages:**
1. `capture` — audio recording committed to disk
2. `transcribe` — transcript JSON written
3. `diarize` — speaker labels applied
4. `generate` — minutes JSON written
5. `ingest` — DB rows upserted
6. `embed` — semantic vectors written
7. `export` — Obsidian / PDF (if configured)

Each stage can be in: `pending | running | succeeded | failed | skipped`.

**Data model.** New table:

```sql
pipeline_stages (
  meeting_id    TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
  stage         TEXT NOT NULL CHECK (stage IN ('capture','transcribe','diarize','generate','ingest','embed','export')),
  status        TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','skipped')),
  started_at    TIMESTAMP,
  finished_at   TIMESTAMP,
  attempt       INTEGER NOT NULL DEFAULT 1,
  last_error    TEXT,         -- short error code or message
  last_error_at TIMESTAMP,
  artifact_path TEXT,          -- disk location of the stage's output
  PRIMARY KEY (meeting_id, stage)
)
```

**Technical approach.**
- New module `src/meeting_minutes/pipeline/state.py`:
  - `mark_running(meeting_id, stage)`, `mark_succeeded(...)`, `mark_failed(meeting_id, stage, error_code, error_msg)`.
  - `next_stage(meeting_id) -> Stage | None` returns the first non-succeeded stage, respecting dependencies.
- Supervisor at server startup (in `main.py` lifespan):
  - Find any `running` stage with no `finished_at` (crashed mid-run).
  - Reset to `failed` with `last_error='interrupted'` (not auto-resumed — user decides).
  - Log summary "N meetings have interrupted stages — see /meeting/:id or run `mm resume`".
- CLI additions:
  - `mm status <meeting_id>` — prints the stage table with colored status.
  - `mm resume <meeting_id>` — runs stages from the first non-succeeded onward.
  - `mm resume --all` — resumes every meeting with a failed/interrupted stage.
- UI: the existing status stepper component reads from `pipeline_stages` and shows per-stage retry buttons; retry calls `POST /api/meetings/:id/resume?from_stage=<stage>`.

**Interaction with retention (DSK-1).** Retention cleanup looks at `pipeline_stages.status` — audio files for meetings where `ingest` has succeeded are eligible for deletion; audio for interrupted pipelines is preserved until the pipeline reaches a terminal state.

**Effort.** Medium (5–7 h).

**Acceptance.**
- Killing the server during the `generate` stage, then restarting, leaves a `failed/interrupted` row in `pipeline_stages` and surfaces a banner in the UI.
- `mm resume <id>` re-runs from `generate` forward; `transcribe` and `diarize` are not re-executed.
- Each stage retry increments `attempt`.
- Retention cleanup skips audio for meetings where any stage is in `pending` or `running` or `failed`.

---

### DSK-1: Disk-Space Preflight with Advisory Cleanup (Modified)

**Status:** Shipped in [PR #4](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/4). Implementation notes: added `mm record start --planned-minutes` and `--force` CLI flags (not in original spec) to wire the non-interactive red-tier refusal path. Watchdog daemon thread is not joined in `stop()` — relies on process exit.

**Modification from initial proposal.** Do **not** refuse to start recording at the 1.2× margin. Instead, **surface a warning with actionable suggestions** — the user always remains in control.

**Problem.** `mm record start` happily fills the disk with FLAC; if free space runs out mid-recording, `sounddevice` errors can corrupt the tail of the audio file.

**Description.** Two-tier check performed before recording starts, plus a mid-recording watchdog:

**Preflight (before recording starts):**
- `estimated_size = planned_or_default_duration * sample_rate * bytes_per_sample * compression_factor`
  - Default planned duration: 60 minutes if unspecified.
  - FLAC compression factor: 0.6 (conservative).
- Compare to `free_disk_space` in the `data_dir` partition.

| Condition | Action |
|---|---|
| free ≥ 2 × estimated | Silent start. |
| 1.2 × ≤ free < 2 × | Yellow warning dialog: "Disk is getting tight. Consider freeing space." Show **top 20 oldest audio files with size + "Delete" checkbox** and a "Delete selected" button. User can dismiss and start recording anyway. |
| free < 1.2 × estimated | Orange warning dialog with the same cleanup UI, stronger copy: "Recording may run out of disk space." User can still confirm start with an explicit "Start anyway" button. |
| free < estimated | Red warning: "Recording will likely fail before completion." Still allows start anyway — with a double-confirm ("Yes, I understand"). |

**Mid-recording watchdog:**
- A thread samples free disk space every 30 s.
- If free space drops below `0.5 × remaining_estimated_size`, triggers **graceful stop** (not abort): flushes the audio buffer, closes the FLAC file cleanly, records the early stop reason on the meeting row, fires a notification.

**Technical approach.**
- Extend `src/meeting_minutes/system1/capture.py`:
  - New `preflight_disk_check()` returning a structured result (`tier`, `free_bytes`, `estimated_bytes`, `oldest_audio`).
  - Watchdog thread in `AudioCaptureEngine.start_recording()` with graceful stop path.
- API: `GET /api/recording/preflight?planned_minutes=<n>` returns the preflight result.
- Cleanup helper: `GET /api/retention/oldest-audio?limit=20` returns files eligible for manual deletion (any audio whose meeting's `ingest` stage has succeeded and whose age > retention threshold).
- UI: existing record page calls preflight before starting; renders the warning dialog with the cleanup table.
- `mm record start` in non-interactive mode (launchd) writes the warning to logs but does not block unless tier is red; red tier in non-interactive mode logs an error and refuses (it is the only place we do refuse — there is no human to acknowledge).

**Effort.** Medium (3–4 h).

**Acceptance.**
- Simulating a near-full disk (via config override of `free_bytes` in tests) surfaces the correct warning tier.
- User can dismiss the warning and start recording anyway for the yellow, orange, and red tiers (interactive).
- Non-interactive mode refuses red-tier starts with a log entry.
- Mid-recording disk-exhaustion triggers a graceful stop producing a valid (truncated) FLAC.

---

### HLT-1: Startup Health Check + Self-Repair

**Status:** Shipped in [PR #4](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/4). Implementation notes: `mm repair` rebuilds `meetings_fts` by drop+recreate (destructive if a user ever extended FTS schema manually; guarded by `--yes` prompt). UI banner consuming `/api/health/full` was deliberately deferred to a follow-up.

**Problem.** Corrupted SQLite (rare; happens on unclean shutdown) or drifted indices (FTS5 / sqlite-vec out of sync with main tables after an interrupted migration) are silent until a query returns wrong results.

**Description.** On `mm serve` startup (and as a CLI command `mm repair [--dry-run]`):

**Checks performed:**
1. `PRAGMA integrity_check` — full SQLite integrity.
2. `meetings_fts` row count matches `meetings` row count.
3. Every `embedding_chunks.chunk_id` has a corresponding `embedding_vectors` row.
4. Every `meetings.meeting_id` with `status='final'` has a `minutes` row.
5. Every audio path referenced by `meetings.audio_file_path` either exists on disk or is marked as retention-deleted.
6. (From SPK-1 once shipped) Every `person_voice_samples.meeting_id` still exists.

**Actions:**
- Each check produces `ok | warn | fail` with a repair recipe.
- On server startup: run all checks, log results, **do not auto-repair** — put a dismissable banner on every page if any check fails.
- `mm repair` with explicit confirm: rebuilds FTS5 index, rebuilds missing embedding vectors (reusing the existing `mm embed` path), orphan-cleanup on sample/attribution tables.
- `mm repair --dry-run` prints the plan without doing anything.

**Technical approach.**
- New module `src/meeting_minutes/health.py`:
  - `check_all() -> HealthReport`
  - `repair(report, dry_run=False) -> RepairLog`
- Reuses the existing `EmbeddingEngine.reindex()` method for vector rebuild; reuses `StorageEngine.rebuild_fts()` for FTS (write it if missing — one function).
- API: `GET /api/health/full` returns the report. (The existing `/healthz` liveness probe stays separate — HLT-1 is a slower, more thorough check.)
- UI banner reads from `GET /api/health/full` at server startup; dismissable per-session.

**Effort.** Medium (3–4 h).

**Acceptance.**
- Deliberately deleting an `embedding_vectors` row makes check 3 fail and `mm repair` restores it.
- Deliberately corrupting `meetings_fts` with a dropped row makes check 2 fail and `mm repair` rebuilds it.
- `mm repair --dry-run` never writes.
- Banner disappears after a successful `mm repair`.

---

### ANA-1: Cross-Meeting Analytics Dashboard

**Status:** Shipped in [PR #6](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/6). Implementation notes: lives in new `stats_analytics.py` module (existing `analytics.py` already holds talk-time analytics). Topic-clusters cache (`topic_clusters_cache` table) was shipped as part of migration 005 rather than left "optional" as the spec suggested. Cluster `topic_summary` is the first chunk's text truncated to 140 chars; LLM-generated summaries are a documented follow-up.

**Problem.** The existing `/stats` page has basic counts (meetings by type/month). The valuable longitudinal views — per-person commitment completion, recurring unresolved topics, sentiment trends, meeting effectiveness trends — are not there, despite the underlying data being present.

**Description.** Extend `/stats` with four new panels, each backed by pure SQL (no new storage):

**Panel 1 — Commitment completion rate per person.**
- For each person: count of action items assigned vs completed, rolling 90 days.
- Display: sortable table with sparkline per person, filters for meeting type and date range.
- Source: `action_items` table joined on `owner`.

**Panel 2 — Recurring unresolved topics.**
- Cluster similar topics by embedding distance (reuse existing `embedding_chunks` where `chunk_type='discussion_point'` or `chunk_type='parking_lot'`).
- Show clusters that appear in ≥ 3 meetings **without** a corresponding decision being recorded.
- Display: list of topic summaries with count + "Meetings where this came up" links.
- Heuristic: clustering via `sqlite-vec` approximate nearest neighbour, threshold 0.8 cosine similarity.

**Panel 3 — Sentiment trend per person / per meeting type.**
- From the existing `StructuredMinutesResponse.participants[].sentiment` and `.sentiment` top-level fields.
- Display: line chart over time, one line per person (selectable) or per meeting type.

**Panel 4 — Meeting-type effectiveness.**
- From the existing `StructuredMinutesResponse.meeting_effectiveness` object (had_clear_agenda, decisions_made, action_items_assigned, unresolved_items).
- Display: bar chart of % of meetings per type with each attribute "yes", rolling 30/90/all-time.

**Technical approach.**
- New API endpoints (all `GET`, all pure queries):
  - `GET /api/stats/commitments?owner=<id>&days=<n>`
  - `GET /api/stats/unresolved-topics?days=<n>&min_count=3`
  - `GET /api/stats/sentiment?person=<id>&type=<type>&days=<n>`
  - `GET /api/stats/effectiveness?type=<type>&days=<n>`
- UI: extend `/stats` page with four new tabbed panels; reuse existing chart library.
- Topic clustering (Panel 2) is the only non-trivial query: precompute nightly (via a scheduled `mm stats rebuild` command) into a temporary `topic_clusters_cache` table to keep the `/stats` page fast. Rebuild takes single-digit seconds on a 1000-meeting corpus.
- Document that ANA-1 panels degrade gracefully when `sqlite-vec` is missing (Panels 1, 3, 4 still work; Panel 2 shows an empty-state hint).

**Effort.** Medium–High (6–8 h) — primarily frontend and the topic-clustering precompute.

**Acceptance.**
- Panel 1 correctly counts open vs completed actions per owner over rolling window.
- Panel 2 surfaces at least one recurring topic in a test corpus with 3+ meetings discussing the same subject without a decision.
- Panel 3 renders a sentiment line with zero meetings gracefully (empty state).
- Panel 4 reflects the existing effectiveness data truthfully.

---

### REC-1: Recurring-Meeting Threading

**Status:** Shipped in [PR #6](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/6). Implementation notes: v1 requires exact attendee-set match rather than 80% overlap (simpler path per spec guidance; 80% overlap is a follow-up). No cron scheduler — detection fires best-effort after each pipeline completion and on-demand via `mm series detect` / `POST /api/series/detect`.

**Problem.** The weekly 1:1 with Jon is 20 separate rows in `meetings` today. No way to view "Jon's 1:1 series."

**Description.** Detect recurring meetings, group them into a **series**, and expose a series view that renders the shared context (decisions, actions, topics) across instances.

**Detection heuristic.**
- Attendee set: exact same set across ≥ 3 meetings, OR ≥ 80% overlap.
- Meeting type: must match.
- Cadence: detected automatically (weekly, biweekly, monthly) by analyzing inter-meeting intervals.
- Run as a nightly precompute, not on every meeting create.

**Data model.** New table:

```sql
meeting_series (
  series_id       INTEGER PRIMARY KEY,
  title           TEXT NOT NULL,          -- auto-generated "1:1 with Jon (weekly)"
  meeting_type    TEXT NOT NULL,
  cadence         TEXT,                   -- 'weekly'|'biweekly'|'monthly'|'irregular'
  attendee_hash   TEXT NOT NULL,          -- stable hash of sorted attendee ids
  created_at      TIMESTAMP NOT NULL,
  last_detected_at TIMESTAMP NOT NULL
);
CREATE UNIQUE INDEX idx_series_signature ON meeting_series(attendee_hash, meeting_type);

-- Join table to handle meetings being re-assigned if the detection heuristic changes
meeting_series_members (
  series_id  INTEGER NOT NULL REFERENCES meeting_series(series_id) ON DELETE CASCADE,
  meeting_id TEXT    NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
  PRIMARY KEY (series_id, meeting_id)
);
```

**Technical approach.**
- New module `src/meeting_minutes/system3/series.py`:
  - `detect_series() -> list[SeriesCandidate]` — nightly job, idempotent.
  - `upsert_series(candidate)` — writes the series + member rows.
- CLI: `mm series detect` — runs detection manually; `mm series list` — lists series.
- API: `GET /api/series`, `GET /api/series/:id` returning series metadata + member meeting list + aggregate stats (open actions across the series, recent decisions, recurring topics).
- UI:
  - New page `/series` listing all detected series.
  - New page `/series/:id` with: member timeline, cross-meeting action-item tracker (carry-over detection), cross-meeting sentiment chart, "Topics that have come up repeatedly" list.
  - Link from each meeting detail page: "Part of series: 1:1 with Jon (weekly) →".
- RAG chat scope: `/chat?series=<id>` limits retrieval to that series's chunks only.

**Interaction with BRF-1.** Briefing page for a person can surface "Your ongoing 1:1 series with Jon — 18 meetings" with a link to `/series/:id`.

**Effort.** Medium–High (6–8 h) including the UI work.

**Acceptance.**
- Creating 4 meetings with the same attendee set and type produces exactly one `meeting_series` row.
- Series page lists all 4 meetings, shows a 4-point timeline, and surfaces any action items that carry over between them.
- Changing the meeting type on one meeting after detection removes it from the series on the next `mm series detect`.

---

### EXP-1: Export to PDF and DOCX

**Status:** Shipped in [PR #8]. PDF renders the stored markdown through markdown-it-py + WeasyPrint; DOCX builds paragraphs from the same markdown and adds the Action Items table from `action_items` rows. `GET /api/meetings/:id/export?format=…` supplements the existing POST endpoint for browser downloads. Bulk series export lives at `GET /api/series/:id/export` and zips the results. Missing native deps yield 501 with an install hint. WeasyPrint's native deps (pango/cairo/gdk-pixbuf/libffi) are installed automatically by `install.sh` and `mm upgrade` on macOS, and `AppConfig.model_post_init()` sets `DYLD_FALLBACK_LIBRARY_PATH` at runtime so the user never has to export it manually. A new `mm doctor` check (#11) reports warn (PDF export is optional) when either the Python package or the native libs are missing.

**Problem.** Obsidian export is great for Obsidian users. Sharing with non-technical stakeholders means copy-pasting markdown into Word.

**Description.** One-click export per meeting to PDF or DOCX, plus CLI variants.

**PDF.**
- Engine: WeasyPrint (HTML + CSS → PDF).
- Template: single stylesheet `templates/export/pdf.css` with minimal, professional styling (serif body, sans-serif headings, page numbers, footer with meeting date).
- Content: title, attendees, summary, key topics, discussion points (collapsible → expanded in PDF), decisions, action items with checkboxes, risks, follow-ups, parking lot. Transcript **excluded** by default (huge); `--with-transcript` flag includes it.

**DOCX.**
- Engine: `python-docx`.
- Structure: same sections as PDF. Action items rendered as a Word table with columns (Description, Owner, Due, Priority, Status).
- Template: optional — users can drop a `templates/export/docx_template.docx` with their corporate styling; if present, paragraphs inherit its styles.

**Triggers.**
- Web UI: "Export" dropdown on meeting detail page → PDF / DOCX / Markdown / Obsidian.
- CLI: `mm export <meeting_id> --format=pdf|docx [--with-transcript] [--out=<path>]`.
- Bulk: `mm export --series=<id> --format=pdf` exports all series members into a single ZIP.

**Technical approach.**
- New module `src/meeting_minutes/export/`:
  - `pdf.py` — markdown → HTML via `markdown-it-py` → WeasyPrint.
  - `docx.py` — structured JSON → `python-docx` composition.
  - `__init__.py` dispatches by format.
- API: `GET /api/meetings/:id/export?format=pdf&with_transcript=false` streams the file with proper Content-Disposition.
- UI: new `ExportMenu.svelte` component; sits next to the existing "Regenerate" button.
- File naming: `{YYYY-MM-DD}_{slug(title)}.pdf|docx`; sanitized to filesystem-safe characters.

**Effort.** Medium (4–6 h).

**Acceptance.**
- Exporting the same meeting to PDF and DOCX produces documents with identical section structure and content.
- `--with-transcript` appends a "Full Transcript" section; default export omits it.
- DOCX template override is respected when present.
- Filename is deterministic given meeting id + format.

---

## Implementation Plan

### Phase 0 — Foundation ✅ Shipped ([PR #3](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/3))

1. **PIP-1** ✅ — Pipeline stage state machine. Ships first because HLT-1, DSK-1, and error-surfacing in the UI read from it. *5–7 h.*

### Phase 1 — Stability & Diagnostics ✅ Shipped ([PR #4](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/4))

2. **HLT-1** ✅ — Startup health check + `mm repair`. *3–4 h.*
3. **DSK-1** ✅ — Disk-space preflight + advisory cleanup + mid-recording watchdog. *3–4 h.*
4. **ONB-1** ✅ — `mm doctor` + `/onboarding`. Reuses HLT-1 check infrastructure. *4–6 h.*

### Phase 2 — Speaker Identity ✅ Shipped ([PR #5](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/5))

5. **SPK-1** ✅ — Passive speaker centroid learning. *6–8 h.*

### Phase 3 — Analytics & Threading ✅ Shipped ([PR #6](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/6))

6. **REC-1** ✅ — Recurring-meeting threading. *6–8 h.*
7. **ANA-1** ✅ — Cross-meeting analytics. *6–8 h.*

### Phase 4 — User Experience ✅ Shipped ([PR #8](https://github.com/PietKonijn007/MeetingMinutesTaker/pull/8))

8. **BRF-1** ✅ — Pre-meeting briefing page + inline record start. *5–7 h.*
9. **NOT-1** ✅ — Desktop notifications. *2 h.*
10. **EXP-1** ✅ — PDF + DOCX export. *4–6 h.*

### Phase 5 — Documentation ✅ Shipped

11. **README.md** ✅ — kept in sync per-phase across PRs #3 through #8; covers all new CLI commands, pages, config keys.
12. **docs/USER_GUIDE.md** ✅ — Batch 3 walk-through added (CLI reference section 6.7, new Web UI pages, seven dedicated how-to sections for onboarding, pipeline resume, health/repair, disk preflight, speaker identity, series, analytics, and a Phase 4 section covering briefing + notifications + export).
13. **docs/SECURITY.md** ✅ — P-1 … P-6 proposed enhancements documented under "Proposed Enhancements (Not Yet Implemented)".

**Total estimated effort:** 45–60 hours. **Actual:** ~33–42 h across 8 PRs (Phases 0–5) — within budget.

### Dependencies Summary

```
PIP-1 ──┬──> HLT-1 ──> ONB-1
        ├──> DSK-1
        └──> (used by UI error-surfacing across the board)

SPK-1 ──> BRF-1 (speaker pre-fill)
REC-1 ──┬──> BRF-1 (series card)
        └──> ANA-1 (series-aware analytics)

NOT-1, EXP-1 — independent, can ship any time after Phase 1
```

### Out of Scope for Batch 3

- All six security enhancements (P-1 to P-6) are **documented in `docs/SECURITY.md` but not scheduled for implementation** at the user's request.
- Multi-user / RBAC.
- Auto-email of minutes or action items.
- Real-time streaming transcription during recording.
- Task-manager integrations (Things, Linear, Jira, etc.).
- Calendar integration.
- Mobile responsive mode.
- i18n / multi-language templates.

### Testing Strategy

Each feature should land with:
- Unit tests for pure logic (state transitions, centroid math, detection heuristics).
- One integration test per feature exercising the API endpoint end-to-end with an in-memory SQLite.
- Manual smoke test documented in the PR description (specific UI flow to click through).

No end-to-end audio-through-LLM tests are required for this batch — that harness is deferred (was B2 in the proposal discussion, not selected here).

---

## Appendix — Unshipped Follow-Ups Surfaced During Batch 3

Items identified during Phases 0–4 but deliberately left for separate PRs. Not part of Batch 3's shipped scope.

### Pipeline (PIP-1)
- **Front-end status stepper component** wired to `/api/meetings/:id/pipeline`. Backend is ready; UI banner for interrupted pipelines is not yet surfaced.
- **Unified session factory** inside `PipelineOrchestrator._track_stage` (currently opens short-lived sessions per call).
- **DIARIZE becomes its own tracked stage** once SPK-1 matures — today it piggybacks on `run_transcription`.

### Health & disk (HLT-1 / DSK-1)
- **UI health banner** consuming `/api/health/full` — backend ships, frontend surface does not.
- **`AudioCaptureEngine` signature cleanup:** the watchdog only activates when the full `AppConfig` is passed; legacy call sites passing only `config.recording` silently skip it.

### Speaker identity (SPK-1)
- **`mm spk1 backfill` command** — seed centroids from pre-SPK-1 meetings in one pass instead of the manual `mm rediarize` → click-save loop.
- **FastAPI `TestClient` coverage** of the augmented `PATCH /transcript/speakers` and `GET /speaker-suggestions` endpoints (currently unit-tested at the function level only).
- **Cold-start hint on `/onboarding`** explaining "the first ~3 meetings still need manual naming."
- **Centroid caching** if users ever accumulate thousands of confirmed samples per person.

### Series & analytics (REC-1 / ANA-1)
- **80%-attendee-overlap matching** for REC-1 — v1 requires exact set match. Needs a cluster-merge story.
- **LLM-generated cluster summaries** for ANA-1 Panel 2 — current implementation truncates the first chunk's text to 140 chars.
- **Nightly scheduler** — today REC-1 detection and `topic_clusters_cache` rebuild fire per-pipeline + on-demand; there is no cron daemon.

### Briefing, notifications, export (BRF-1 / NOT-1 / EXP-1)
- **`mm://meeting/{id}` URL scheme** registration in `install.sh`; NOT-1 click URLs currently use `http://localhost:8080/...` (works, but doesn't focus an existing tab).
- **Web-UI toast mirror** of the macOS notification so non-macOS users get equivalent feedback.
- **Per-user corporate DOCX template picker** in Settings (currently hard-coded to `templates/export/docx_template.docx` if present).

### Security
- **P-1 through P-6** in `docs/SECURITY.md` remain documented proposals, not implemented.
