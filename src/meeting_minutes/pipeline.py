"""Pipeline orchestrator — coordinates Systems 1, 2, and 3."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from meeting_minutes.config import AppConfig, resolve_db_path
from meeting_minutes.logging import get_logger
from meeting_minutes.pipeline_state import (
    Stage,
    Status,
    get_stages,
    mark_failed,
    mark_running,
    mark_succeeded,
    next_stage as _state_next_stage,
)


def _console(msg: str, style: str = "") -> None:
    """Print a formatted status line to console."""
    try:
        from rich.console import Console
        _c = Console(stderr=True)
        _c.print(msg, style=style, highlight=False)
    except ImportError:
        print(msg)


CUSTOM_MODELS_PATH = Path(__file__).parent.parent.parent / "config" / "custom_models.json"

# Built-in models that don't need to be tracked as custom
_BUILTIN_MODELS: dict[str, set[str]] = {
    "anthropic": {"claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"},
    "openai": {"gpt-4o", "gpt-4o-mini"},
    "openrouter": {
        "anthropic/claude-sonnet-4", "anthropic/claude-haiku-4",
        "google/gemini-2.5-pro-preview", "google/gemini-2.5-flash-preview",
        "openai/gpt-4o", "openai/gpt-4o-mini",
        "meta-llama/llama-4-maverick", "deepseek/deepseek-r1",
        "mistralai/mistral-medium-3",
    },
    "ollama": set(),
}


def _record_successful_model(provider: str, model: str) -> None:
    """Record a successfully used model to config/custom_models.json if it's not built-in."""
    import json as _json

    if model in _BUILTIN_MODELS.get(provider, set()):
        return

    try:
        data: dict[str, list[str]] = {}
        if CUSTOM_MODELS_PATH.exists():
            data = _json.loads(CUSTOM_MODELS_PATH.read_text())

        provider_models = data.setdefault(provider, [])
        if model not in provider_models:
            provider_models.append(model)
            CUSTOM_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
            CUSTOM_MODELS_PATH.write_text(_json.dumps(data, indent=2) + "\n")
    except Exception:
        pass  # Non-critical — don't break pipeline for model tracking


class PipelineOrchestrator:
    """Coordinate recording → transcription → generation → ingestion."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._logger = get_logger("pipeline")
        self._data_dir = Path(config.data_dir).expanduser()
        self._recordings_dir = self._data_dir / "recordings"
        self._transcripts_dir = self._data_dir / "transcripts"
        self._minutes_dir = self._data_dir / "minutes"

    # -----------------------------------------------------------------------
    # Pipeline state helpers (PIP-1)
    # -----------------------------------------------------------------------

    def _state_session(self):
        """Open a short-lived SQLAlchemy session for pipeline_stages writes."""
        from meeting_minutes.system3.db import get_session_factory

        db_path = resolve_db_path(self._config.storage.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        session_factory = get_session_factory(f"sqlite:///{db_path}")
        return session_factory()

    # -----------------------------------------------------------------------
    # SPK-1: speaker identity
    # -----------------------------------------------------------------------

    def _spk1_process(
        self,
        *,
        meeting_id: str,
        diarize_engine,
        diarization_result,
    ) -> dict[str, dict]:
        """Match diarization clusters against known person centroids and
        persist unconfirmed voice samples for later confirmation.

        Returns a ``{cluster_id: suggestion_dict}`` map suitable for the
        transcript writer. Silently skips clusters with < 5 s of speech
        and clusters the pipeline did not produce embeddings for (either
        because the pyannote version is too old, or embeddings failed).
        """
        from meeting_minutes.system1 import speaker_identity as si
        from meeting_minutes.system3.db import get_session_factory

        embeddings = getattr(diarize_engine, "last_cluster_embeddings", {}) or {}
        if not embeddings or not diarization_result or not diarization_result.segments:
            return {}

        db_path = resolve_db_path(self._config.storage.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        session_factory = get_session_factory(f"sqlite:///{db_path}")
        session = session_factory()

        suggestions_by_cluster: dict[str, dict] = {}
        try:
            # Drop clusters below the minimum speech threshold — they get no
            # sample row and no suggestion.
            eligible: dict[str, "np.ndarray"] = {}  # type: ignore[name-defined]
            for cluster_id, vec in embeddings.items():
                if si.min_speech_duration_ok(
                    diarization_result.segments, cluster_id,
                ):
                    eligible[cluster_id] = vec

            if not eligible:
                return {}

            matches = si.match_clusters(session, eligible)

            for cluster_id, vec in eligible.items():
                match = matches.get(cluster_id)
                if match and match.person_id is not None:
                    # Persist an *unconfirmed* sample under the suggested
                    # person; confirm_sample() flips the flag once the
                    # user accepts.
                    try:
                        si.write_sample(
                            session,
                            person_id=match.person_id,
                            meeting_id=meeting_id,
                            cluster_id=cluster_id,
                            embedding=vec,
                            confirmed=False,
                        )
                    except Exception as w_exc:
                        self._logger.warning(
                            "Could not write unconfirmed sample for cluster %s: %s",
                            cluster_id, w_exc,
                        )

                if match is not None:
                    suggestions_by_cluster[cluster_id] = {
                        "suggested_person_id": match.person_id,
                        "suggested_name": match.person_name,
                        "suggestion_score": round(match.score, 4),
                        "suggestion_tier": match.tier,
                    }

            if suggestions_by_cluster:
                n_high = sum(1 for s in suggestions_by_cluster.values() if s["suggestion_tier"] == "high")
                n_med = sum(1 for s in suggestions_by_cluster.values() if s["suggestion_tier"] == "medium")
                if n_high or n_med:
                    _console(
                        f"  ✓ Speaker suggestions: {n_high} high-confidence, {n_med} medium-confidence",
                        "green",
                    )
        finally:
            session.close()

        return suggestions_by_cluster

    @contextmanager
    def _track_stage(self, meeting_id: str, stage: Stage):
        """Wrap a stage: mark running, mark succeeded on clean return, mark
        failed on exception (and re-raise).

        Caller can set ``ctx.artifact_path`` on the yielded object to store
        the artifact path on the succeeded row.
        """

        class _Ctx:
            artifact_path: str | None = None

        ctx = _Ctx()
        session = self._state_session()
        try:
            mark_running(session, meeting_id, stage)
        finally:
            session.close()

        try:
            yield ctx
        except Exception as exc:
            session = self._state_session()
            try:
                mark_failed(session, meeting_id, stage, str(exc))
            finally:
                session.close()
            # NOT-1: fire a desktop notification on stage failure. Wrap
            # defensively — notifications must never break the pipeline.
            try:
                self._notify_failed(meeting_id, stage.value, str(exc))
            except Exception as notif_exc:  # pragma: no cover - defensive
                self._logger.debug("Failure notification dispatch failed: %s", notif_exc)
            raise
        else:
            session = self._state_session()
            try:
                mark_succeeded(session, meeting_id, stage, artifact_path=ctx.artifact_path)
            finally:
                session.close()

    # -----------------------------------------------------------------------
    # NOT-1: desktop notification helpers
    # -----------------------------------------------------------------------

    def _meeting_summary_for_notification(self, meeting_id: str) -> tuple[str, str | None, int | None]:
        """Return ``(title, duration, open_action_count)`` for a meeting, best-effort.

        All values degrade to short placeholders if the DB row is missing.
        """
        from meeting_minutes.system3.db import ActionItemORM, MeetingORM, get_session_factory

        db_path = resolve_db_path(self._config.storage.sqlite_path)
        session_factory = get_session_factory(f"sqlite:///{db_path}")
        session = session_factory()
        try:
            m = session.get(MeetingORM, meeting_id)
            if m is None:
                return (f"Meeting {meeting_id[:8]}", None, None)
            action_count = (
                session.query(ActionItemORM)
                .filter(ActionItemORM.meeting_id == meeting_id)
                .count()
            )
            return (m.title or f"Meeting {meeting_id[:8]}", m.duration, action_count)
        finally:
            session.close()

    def _notify_complete(self, meeting_id: str) -> None:
        """Fire a pipeline-complete desktop notification. Never raises."""
        try:
            from meeting_minutes.notifications import notify_pipeline_complete

            title, duration, action_count = self._meeting_summary_for_notification(meeting_id)
            notify_pipeline_complete(
                meeting_id,
                title,
                duration=duration,
                action_count=action_count,
                config=self._config.notifications,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.debug("Completion notification failed: %s", exc)

    def _notify_failed(self, meeting_id: str, stage: str, error: str) -> None:
        """Fire a pipeline-failed desktop notification. Never raises."""
        try:
            from meeting_minutes.notifications import notify_pipeline_failed

            # The meeting may not yet exist in the DB (e.g. ingest failed
            # on the first run), so fall back to the id short form.
            try:
                title, _, _ = self._meeting_summary_for_notification(meeting_id)
            except Exception:
                title = f"Meeting {meeting_id[:8]}"
            notify_pipeline_failed(
                meeting_id,
                title,
                stage=stage,
                error=error,
                config=self._config.notifications,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.debug("Failure notification failed: %s", exc)

    # -----------------------------------------------------------------------
    # Retry helper
    # -----------------------------------------------------------------------

    async def _retry_async(self, func, *args, max_retries=2, base_delay=5, step_name=""):
        """Retry an async function with exponential backoff."""
        for attempt in range(max_retries + 1):
            try:
                return await func(*args)
            except Exception as exc:
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    self._logger.warning("Step '%s' failed (attempt %d/%d): %s — retrying in %ds",
                                         step_name, attempt + 1, max_retries + 1, exc, delay)
                    _console(f"  ⚠ {step_name} failed (attempt {attempt + 1}): {exc} — retrying in {delay}s", "yellow")
                    await asyncio.sleep(delay)
                else:
                    self._logger.error("Step '%s' failed after %d attempts: %s", step_name, max_retries + 1, exc)
                    raise

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def run_full_pipeline(self, meeting_id: str | None = None) -> str:
        """Run the full pipeline: record → transcribe → generate → ingest."""
        if meeting_id is None:
            meeting_id = str(uuid.uuid4())

        pipeline_start = time.time()
        self._logger.info("Pipeline start — meeting %s", meeting_id)
        _console(f"\n{'='*60}", "bold")
        _console(f"  PIPELINE START — Meeting {meeting_id[:12]}...", "bold green")
        _console(f"{'='*60}\n")

        _console("[1/3] Transcription...", "bold cyan")
        self._logger.info("[1/3] Transcription for %s", meeting_id)
        with self._track_stage(meeting_id, Stage.TRANSCRIBE) as ctx:
            transcript_path = await self._retry_async(
                self.run_transcription, meeting_id,
                max_retries=2, base_delay=5, step_name="Transcription",
            )
            ctx.artifact_path = str(transcript_path)
        # Diarization runs inside run_transcription; record its state here.
        with self._track_stage(meeting_id, Stage.DIARIZE) as ctx:
            ctx.artifact_path = str(transcript_path)

        _console("\n[2/3] Minutes generation...", "bold cyan")
        self._logger.info("[2/3] Minutes generation for %s", meeting_id)
        with self._track_stage(meeting_id, Stage.GENERATE) as ctx:
            minutes_path = await self._retry_async(
                self.run_generation, meeting_id,
                max_retries=3, base_delay=3, step_name="Generation",
            )
            ctx.artifact_path = str(minutes_path)

        _console("\n[3/3] Database ingestion...", "bold cyan")
        self._logger.info("[3/3] Database ingestion for %s", meeting_id)
        with self._track_stage(meeting_id, Stage.INGEST):
            await self._retry_async(
                self.run_ingestion, meeting_id,
                max_retries=1, base_delay=2, step_name="Ingestion",
            )

        # REC-1: best-effort recurring-meeting detection. Same pattern as
        # SPK-1 — failures must not break the pipeline.
        try:
            self._detect_series()
        except Exception as exc:
            self._logger.warning("REC-1 series detection failed: %s", exc)

        # Embed for semantic search (best-effort, non-blocking).
        _console("  Indexing for semantic search...", "dim")
        try:
            with self._track_stage(meeting_id, Stage.EMBED):
                await asyncio.get_event_loop().run_in_executor(
                    None, self._embed_meeting, meeting_id
                )
        except Exception as exc:
            self._logger.warning("Embedding failed for %s: %s — semantic search may be incomplete", meeting_id, exc)
            _console(f"  ⚠ Embedding failed: {exc}", "yellow")

        # Post-processing: backup and Obsidian export.
        self._maybe_backup()
        try:
            with self._track_stage(meeting_id, Stage.EXPORT):
                self._export_to_obsidian_from_file(meeting_id)
        except Exception as exc:
            self._logger.warning("Obsidian export failed for %s: %s", meeting_id, exc)

        # Retention cleanup
        from meeting_minutes.retention import enforce_retention
        enforce_retention(self._config)

        elapsed = time.time() - pipeline_start
        self._logger.info("Pipeline complete — %s — %.1fs total", meeting_id, elapsed)
        _console(f"\n{'='*60}", "bold")
        _console(f"  PIPELINE COMPLETE — {elapsed:.1f}s total", "bold green")
        _console(f"  Meeting ID: {meeting_id}", "dim")
        _console(f"{'='*60}\n")

        # NOT-1: fire a "meeting ready" desktop notification. Defensive —
        # notifications must never break the pipeline.
        self._notify_complete(meeting_id)

        return meeting_id

    async def run_transcription(self, meeting_id: str) -> Path:
        """Run System 1: transcribe audio → produce transcript JSON."""
        from meeting_minutes.system1.output import TranscriptJSONWriter
        from meeting_minutes.system1.transcribe import get_transcription_engine
        from meeting_minutes.system1.diarize import DiarizationEngine

        self._transcripts_dir.mkdir(parents=True, exist_ok=True)

        # Check if transcript already exists
        transcript_path = self._transcripts_dir / f"{meeting_id}.json"
        if transcript_path.exists():
            _console(f"  ✓ Transcript already exists: {transcript_path.name}", "dim")
            return transcript_path

        # Look for audio file
        audio_path = self._recordings_dir / f"{meeting_id}.flac"
        if not audio_path.exists():
            for ext in (".wav", ".mp3", ".m4a", ".ogg"):
                candidate = self._recordings_dir / f"{meeting_id}{ext}"
                if candidate.exists():
                    audio_path = candidate
                    break
            else:
                raise FileNotFoundError(
                    f"No audio file found for meeting {meeting_id} in {self._recordings_dir}"
                )

        # Audio file info
        audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
        _console(f"  Audio file: {audio_path.name} ({audio_size_mb:.1f} MB)")

        # Transcribe
        engine = get_transcription_engine(self._config.transcription)
        model_name = self._config.transcription.whisper_model
        lang = self._config.transcription.language
        engine_name = self._config.transcription.primary_engine
        _console(f"  Engine: {engine_name} | Model: {model_name} | Language: {lang}")
        _console(f"  Transcribing...", "yellow")

        t0 = time.time()
        result = await asyncio.get_event_loop().run_in_executor(
            None, engine.transcribe, audio_path
        )
        result.meeting_id = meeting_id
        t_transcribe = time.time() - t0

        num_segments = len(result.segments)
        duration = result.segments[-1].end if result.segments else 0.0
        total_words = sum(len(s.text.split()) for s in result.segments)
        self._logger.info("Transcription done in %.1fs — segments=%d words=%d duration=%.0fs", t_transcribe, num_segments, total_words, duration)
        _console(f"  ✓ Transcription done in {t_transcribe:.1f}s", "green")
        _console(f"    Segments: {num_segments} | Words: {total_words} | Audio duration: {duration:.0f}s")
        _console(f"    Language detected: {result.language}")
        if result.segments:
            preview = result.segments[0].text[:100]
            _console(f"    First segment: \"{preview}...\"", "dim")

        # Diarize
        diarization_result = None
        speaker_suggestions: dict[str, dict] = {}
        diarize_engine: DiarizationEngine | None = None
        if self._config.diarization.enabled:
            _console(f"  Speaker diarization...", "yellow")
            self._logger.info("Speaker diarization starting (engine=%s)", self._config.diarization.engine)
            if not os.environ.get("HF_TOKEN"):
                self._logger.warning("HF_TOKEN environment variable not set — diarization will likely fail. Get a token at huggingface.co/settings/tokens and accept the license at huggingface.co/pyannote/speaker-diarization-3.1")
            diarize_engine = DiarizationEngine(self._config.diarization)
            try:
                t0 = time.time()
                diarization_result = await asyncio.get_event_loop().run_in_executor(
                    None, diarize_engine.diarize, audio_path
                )
                diarization_result.meeting_id = meeting_id
                t_diarize = time.time() - t0
                n_speakers = len(set(s.speaker for s in diarization_result.segments))
                n_segments = len(diarization_result.segments)
                if n_segments == 0:
                    self._logger.warning("Diarization returned 0 segments in %.1fs — speaker labels will be missing. Check HF_TOKEN and pyannote license acceptance.", t_diarize)
                    _console(f"  ⚠ Diarization returned 0 segments in {t_diarize:.1f}s — check HF_TOKEN", "yellow")
                else:
                    self._logger.info("Diarization done in %.1fs — segments=%d speakers=%d", t_diarize, n_segments, n_speakers)
                    _console(f"  ✓ Diarization done in {t_diarize:.1f}s — {n_speakers} speaker(s) detected", "green")

                    # Apply user-provided speaker names (in order of first-speaking)
                    import json as _json
                    notes_file = self._data_dir / "notes" / f"{meeting_id}.json"
                    if notes_file.exists():
                        try:
                            _notes = _json.loads(notes_file.read_text())
                            _user_names = _notes.get("speakers") or []
                            # Handle comma-separated string or list
                            if isinstance(_user_names, str):
                                _user_names = [n.strip() for n in _user_names.split(",") if n.strip()]
                            if _user_names:
                                mapping = DiarizationEngine.apply_speaker_names(diarization_result, _user_names)
                                if mapping:
                                    self._logger.info("Mapped speakers: %s", mapping)
                                    _console(f"  ✓ Mapped speakers: {', '.join(f'{k}→{v}' for k,v in mapping.items())}", "green")
                        except Exception as exc:
                            self._logger.warning("Could not apply user speaker names: %s", exc)

                # SPK-1: speaker centroid matching + unconfirmed sample persistence.
                try:
                    speaker_suggestions = self._spk1_process(
                        meeting_id=meeting_id,
                        diarize_engine=diarize_engine,
                        diarization_result=diarization_result,
                    )
                except Exception as spk_exc:
                    self._logger.warning("SPK-1 processing failed: %s — continuing without suggestions", spk_exc)
                    _console(f"  ⚠ Speaker identity step skipped: {spk_exc}", "dim")
            except Exception as exc:
                self._logger.warning("Diarization failed: %s — continuing without", exc)
                _console(f"  ⚠ Diarization failed: {exc} — continuing without", "yellow")
        else:
            self._logger.info("Diarization disabled in config — skipping")
            _console(f"  Diarization: disabled", "dim")

        # Write transcript JSON
        from meeting_minutes.models import AudioRecordingResult
        from datetime import datetime, timezone

        recording_result = AudioRecordingResult(
            meeting_id=meeting_id,
            audio_file=str(audio_path),
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            duration_seconds=duration,
            sample_rate=self._config.recording.sample_rate,
            recording_device=self._config.recording.audio_device,
        )

        writer = TranscriptJSONWriter()
        transcript_path = writer.write(
            meeting_id=meeting_id,
            recording=recording_result,
            transcription=result,
            diarization=diarization_result,
            output_dir=self._transcripts_dir,
            speaker_suggestions=speaker_suggestions,
        )

        _console(f"  ✓ Transcript saved: {transcript_path.name}", "green")

        # Encrypt transcript if configured
        if self._config.security.encryption_enabled and self._config.security.encryption_key:
            from meeting_minutes.encryption import encrypt_file
            encrypt_file(transcript_path, self._config.security.encryption_key)
            _console(f"  🔒 Transcript encrypted", "dim")

        return transcript_path

    async def run_generation(self, meeting_id: str) -> Path:
        """Run System 2: transcript → minutes."""
        from meeting_minutes.models import MeetingContext
        from meeting_minutes.system2.ingest import TranscriptIngester
        from meeting_minutes.system2.llm_client import LLMClient
        from meeting_minutes.system2.output import MinutesJSONWriter
        from meeting_minutes.system2.parser import MinutesParser
        from meeting_minutes.system2.prompts import PromptTemplateEngine
        from meeting_minutes.system2.quality import QualityChecker
        from meeting_minutes.system2.router import PromptRouter

        self._minutes_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = self._transcripts_dir / f"{meeting_id}.json"
        if not transcript_path.exists():
            raise FileNotFoundError(
                f"Transcript not found for meeting {meeting_id}: {transcript_path}"
            )

        # Ingest transcript
        _console(f"  Loading transcript: {transcript_path.name}")
        ingester = TranscriptIngester()
        transcript_data = ingester.ingest(transcript_path)
        tj = transcript_data.transcript_json
        _console(f"    Speakers: {len(tj.speakers)} | Transcript length: {len(transcript_data.full_text):,} chars")

        # Route to template
        gen_config = self._config.generation
        templates_dir = Path(gen_config.templates_dir)
        if not templates_dir.is_absolute():
            templates_dir = Path(__file__).parent.parent.parent / gen_config.templates_dir

        router = PromptRouter(gen_config, templates_dir)

        # Classify meeting type using LLM if confidence is low
        if tj.meeting_type_confidence < router.CONFIDENCE_THRESHOLD:
            _console(f"  Classifying meeting type with LLM (was: {tj.meeting_type}, confidence: {tj.meeting_type_confidence:.2f})...")
            try:
                # Extract calendar title if available
                calendar_title = ""
                if hasattr(tj, "calendar") and tj.calendar:
                    calendar_title = getattr(tj.calendar, "title", "") or ""

                classified_type, classified_conf, reasoning = await router.classify_with_llm(
                    transcript_excerpt=transcript_data.full_text[:4000],
                    num_speakers=len(tj.speakers),
                    calendar_title=calendar_title,
                    num_attendees=len(tj.speakers),
                )
                _console(f"  ✓ LLM classified: {classified_type} (confidence: {classified_conf:.2f})")
                _console(f"    Reasoning: {reasoning}", "dim")

                # Use the LLM classification
                tj.meeting_type = classified_type
                tj.meeting_type_confidence = classified_conf
            except Exception as e:
                _console(f"  ⚠ LLM classification failed: {e}", "yellow")

        _console(f"    Meeting type: {tj.meeting_type} (confidence: {tj.meeting_type_confidence:.2f})")

        template = router.select_template(
            tj.meeting_type,
            tj.meeting_type_confidence,
        )
        _console(f"  Template selected: {template.name}")

        # Load user notes and speaker names if available
        import json as _json
        user_notes = ""
        user_speakers = []
        user_instructions = ""
        notes_file = self._data_dir / "notes" / f"{meeting_id}.json"
        if notes_file.exists():
            try:
                notes_data = _json.loads(notes_file.read_text())
                user_notes = notes_data.get("notes", "")
                user_speakers = notes_data.get("speakers", [])
                user_instructions = notes_data.get("instructions", "")
                if user_notes:
                    _console(f"  User notes: {len(user_notes)} chars")
                if user_speakers:
                    _console(f"  Speaker names provided: {', '.join(user_speakers)}")
                if user_instructions:
                    _console(f"  Custom instructions: {user_instructions[:100]}...")
            except Exception:
                pass

        # Build context — prefer user-provided speaker names over diarized labels
        attendees = user_speakers if user_speakers else [s.name or s.label for s in tj.speakers]
        context = MeetingContext(
            meeting_id=meeting_id,
            title=f"Meeting {meeting_id[:8]}",
            date=tj.metadata.timestamp_start.strftime("%Y-%m-%d"),
            duration=f"{int(tj.metadata.duration_seconds // 60)} minutes",
            attendees=attendees,
            meeting_type=tj.meeting_type,
        )
        _console(f"  Context: {context.date} | {context.duration} | {len(attendees)} attendees")

        # Prior-action carryover (ACT-1): pull still-open items from recent
        # meetings that share attendees so the LLM can detect acknowledged
        # closures in this meeting. Best-effort — a DB hiccup here must not
        # block generation.
        prior_actions_payload: list[dict] = []
        if gen_config.close_acknowledged_actions and attendees:
            try:
                from meeting_minutes.system3.db import get_session_factory
                from meeting_minutes.system3.storage import StorageEngine as _StorageEngine

                db_path = resolve_db_path(self._config.storage.sqlite_path)
                if db_path.exists():
                    session_factory = get_session_factory(f"sqlite:///{db_path}")
                    _session = session_factory()
                    try:
                        _storage = _StorageEngine(_session)
                        items = _storage.get_open_action_items_for_attendees(
                            attendee_names=attendees,
                            lookback_meetings=gen_config.prior_actions_lookback_meetings,
                            exclude_meeting_id=meeting_id,
                        )
                        for it in items:
                            m_title = it.meeting.title if it.meeting else None
                            prior_actions_payload.append({
                                "id": it.action_item_id,
                                "description": it.description,
                                "owner": it.owner,
                                "due_date": it.due_date,
                                "meeting_title": m_title,
                            })
                    finally:
                        _session.close()
                    if prior_actions_payload:
                        _console(
                            f"  Prior open actions carried forward: {len(prior_actions_payload)}",
                            "dim",
                        )
            except Exception as _exc:
                self._logger.debug("Prior-action carryover skipped: %s", _exc)

        # Render prompt and generate — inject user notes if available
        prompt_engine = PromptTemplateEngine(templates_dir)
        provider = gen_config.llm.primary_provider
        model = gen_config.llm.model
        llm_client = LLMClient(gen_config.llm)

        # Enhance transcript with user notes
        enhanced_transcript = transcript_data.full_text
        if user_notes:
            enhanced_transcript = (
                f"{transcript_data.full_text}\n\n"
                f"---\n"
                f"## Organizer's Meeting Notes\n"
                f"The following notes were taken by the meeting organizer during the meeting. "
                f"These notes reflect the organizer's priorities and observations. "
                f"IMPORTANT: Include these notes as a dedicated section called "
                f"'Organizer Notes' in the meeting minutes output. "
                f"Also use them to enhance other sections — they capture context "
                f"that may not be obvious from the transcript alone.\n\n"
                f"{user_notes}"
            )
            _console(f"  Enhanced transcript with user notes ({len(user_notes)} chars)")

        # Build custom system prompt additions from user instructions
        custom_system_addendum = ""
        if user_instructions:
            custom_system_addendum = (
                f"\n\n## Additional Instructions from the Meeting Organizer\n"
                f"The meeting organizer has provided these specific instructions. "
                f"Follow them carefully in addition to your standard analysis:\n\n"
                f"{user_instructions}"
            )

        # Build extra template vars: vendor list, length mode, prior actions.
        extra_vars = {
            "vendors": list(gen_config.vendors or []),
            "length_mode": gen_config.length_mode or "concise",
            "prior_actions": prior_actions_payload,
        }

        # Try structured generation first
        try:
            _console(f"  Trying structured generation (tool_use)...", "yellow")
            system_prompt, user_prompt = prompt_engine.render_structured(
                template, context, enhanced_transcript, extra_vars=extra_vars,
            )
            if custom_system_addendum:
                system_prompt += custom_system_addendum
            _console(f"  Prompt rendered: {len(user_prompt):,} chars")
            _console(f"  Calling LLM: {provider} / {model}...", "yellow")

            t0 = time.time()
            llm_response = await llm_client.generate_structured(user_prompt, system_prompt)
            t_llm = time.time() - t0

            self._logger.info("LLM structured response in %.1fs — provider=%s model=%s tokens_in=%d tokens_out=%d cost=$%.4f",
                              t_llm, llm_response.provider, llm_response.model,
                              llm_response.input_tokens, llm_response.output_tokens, llm_response.cost_usd)
            _console(f"  ✓ LLM response received in {t_llm:.1f}s", "green")
            _console(f"    Provider: {llm_response.provider} | Model: {llm_response.model}")
            _console(f"    Tokens: {llm_response.input_tokens:,} in + {llm_response.output_tokens:,} out")
            _console(f"    Cost: ${llm_response.cost_usd:.4f}")

            # Parse structured response
            from meeting_minutes.models import StructuredMinutesResponse
            from meeting_minutes.system2.parser import StructuredMinutesAdapter

            structured = StructuredMinutesResponse(**llm_response.structured_data)
            adapter = StructuredMinutesAdapter()
            parsed_minutes = adapter.adapt(structured, context)

            # Use LLM-generated title
            if parsed_minutes.title:
                context.title = parsed_minutes.title

            # N11: Meeting type refinement — check if LLM suggests a different type.
            # Only accept suggestions that match a known MeetingType enum value;
            # the LLM often returns free-form phrases like "Campaign Kickoff /
            # Marketing Planning" which would otherwise be stored as a raw string
            # and render as "Other" (since no badge color matches it).
            if hasattr(structured, 'meeting_type_suggestion') and structured.meeting_type_suggestion:
                from meeting_minutes.models import MeetingType
                _valid_types = {t.value for t in MeetingType}
                raw_suggestion = structured.meeting_type_suggestion.strip()
                # Normalize: lowercase, take first token before any separator
                normalized = raw_suggestion.lower().split("/")[0].split("(")[0].strip().replace(" ", "_")
                if normalized in _valid_types and normalized != tj.meeting_type and normalized != "other":
                    self._logger.info(
                        "Meeting type refinement: classified as '%s' but content suggests '%s' (normalized to '%s')",
                        tj.meeting_type, raw_suggestion, normalized,
                    )
                    _console(
                        f"  ℹ Type refinement: classified as '{tj.meeting_type}' but content suggests '{normalized}'",
                        "dim",
                    )
                    context.meeting_type = normalized
                elif raw_suggestion and normalized not in _valid_types:
                    # LLM returned a free-form suggestion — log it but don't use it
                    self._logger.info(
                        "Meeting type refinement: LLM suggested '%s' (keeping '%s' — not a known type)",
                        raw_suggestion, tj.meeting_type,
                    )
                    _console(
                        f"  ℹ LLM suggested free-form type '{raw_suggestion}' — keeping '{tj.meeting_type}'",
                        "dim",
                    )

            _console(f"  ✓ Structured generation succeeded", "green")
            _console(f"    Title: {parsed_minutes.title}")
            _console(f"    Sentiment: {parsed_minutes.sentiment}")
            _console(f"    Discussion points: {len(parsed_minutes.discussion_points)}")
            _console(f"    Decisions: {len(parsed_minutes.decisions)}")
            _console(f"    Action items: {len(parsed_minutes.action_items)}")
            _console(f"    Risks: {len(parsed_minutes.risks_and_concerns)}")
            _console(f"    Follow-ups: {len(parsed_minutes.follow_ups)}")
            _console(f"    Parking lot: {len(parsed_minutes.parking_lot)}")

        except Exception as structured_exc:
            self._logger.warning("Structured generation failed: %s — falling back to text+regex", structured_exc)
            _console(f"  ⚠ Structured generation failed: {structured_exc}", "yellow")
            _console(f"  Falling back to text+regex path...", "yellow")

            # Fall back to old text+regex path
            full_prompt = prompt_engine.render(
                template, context, enhanced_transcript, extra_vars=extra_vars,
            )
            _console(f"  Prompt rendered: {len(full_prompt):,} chars")
            _console(f"  Calling LLM: {provider} / {model}...", "yellow")

            t0 = time.time()
            fallback_system = template.system_prompt
            if custom_system_addendum:
                fallback_system += custom_system_addendum
            llm_response = await llm_client.generate(full_prompt, system_prompt=fallback_system)
            t_llm = time.time() - t0

            _console(f"  ✓ LLM response received in {t_llm:.1f}s", "green")
            _console(f"    Provider: {llm_response.provider} | Model: {llm_response.model}")
            _console(f"    Tokens: {llm_response.input_tokens:,} in + {llm_response.output_tokens:,} out")
            _console(f"    Cost: ${llm_response.cost_usd:.4f}")
            _console(f"    Response length: {len(llm_response.text):,} chars")

            # Parse response
            _console(f"  Parsing LLM response...")
            parser = MinutesParser()
            parsed_minutes = parser.parse(llm_response.text, context)

            if parsed_minutes.title:
                context.title = parsed_minutes.title

        _console(f"    Title: {parsed_minutes.title}")
        _console(f"    Summary: {len(parsed_minutes.summary)} chars")
        _console(f"    Sections: {len(parsed_minutes.sections)}")
        _console(f"    Action items: {len(parsed_minutes.action_items)}")
        _console(f"    Decisions: {len(parsed_minutes.decisions)}")
        _console(f"    Key topics: {parsed_minutes.key_topics}")

        # Quality check
        _console(f"  Running quality checks...")
        quality_checker = QualityChecker()
        quality_report = quality_checker.check(parsed_minutes, transcript_data)

        coverage_ok = quality_report.speaker_coverage >= 0.8
        ratio_ok = 0.05 <= quality_report.length_ratio <= 0.5
        hallucination_ok = len(quality_report.hallucination_flags) == 0

        for check_name, passed, detail in [
            ("Speaker coverage", coverage_ok, f"{quality_report.speaker_coverage:.0%}"),
            ("Length ratio", ratio_ok, f"{quality_report.length_ratio:.0%}"),
            ("Hallucination check", hallucination_ok, f"{len(quality_report.hallucination_flags)} flags"),
        ]:
            icon = "✓" if passed else "⚠"
            color = "green" if passed else "yellow"
            _console(f"    {icon} {check_name} ({detail})", color)
        if quality_report.hallucination_flags:
            for flag in quality_report.hallucination_flags[:5]:
                _console(f"      → {flag}", "dim")
        if quality_report.issues:
            for issue in quality_report.issues[:5]:
                _console(f"      → {issue.message}", "dim")
        _console(f"    Overall: {'PASS' if quality_report.passed else 'NEEDS REVIEW'} (score: {quality_report.score:.2f})")

        # Write output
        writer = MinutesJSONWriter()
        meeting_ctx_dict = {
            "title": context.title,
            "date": context.date,
            "duration": context.duration,
            "attendees": context.attendees,
            "organizer": context.organizer,
            "meeting_type": context.meeting_type,
        }
        json_path, md_path = writer.write(
            minutes=parsed_minutes,
            quality_report=quality_report,
            llm_response=llm_response,
            output_dir=self._minutes_dir,
            meeting_context=meeting_ctx_dict,
        )

        _console(f"  ✓ Minutes saved: {json_path.name}", "green")
        _console(f"  ✓ Markdown saved: {md_path.name}", "green")

        # Record successful model usage for custom model persistence
        _record_successful_model(llm_response.provider, llm_response.model)

        # Encrypt minutes files if configured
        if self._config.security.encryption_enabled and self._config.security.encryption_key:
            from meeting_minutes.encryption import encrypt_file
            encrypt_file(json_path, self._config.security.encryption_key)
            encrypt_file(md_path, self._config.security.encryption_key)
            _console(f"  🔒 Minutes encrypted", "dim")

        return json_path

    async def run_ingestion(self, meeting_id: str) -> None:
        """Run System 3: ingest minutes into database."""
        from meeting_minutes.system3.db import get_session_factory
        from meeting_minutes.system3.ingest import MinutesIngester
        from meeting_minutes.system3.search import SearchEngine
        from meeting_minutes.system3.storage import StorageEngine

        minutes_path = self._minutes_dir / f"{meeting_id}.json"
        if not minutes_path.exists():
            raise FileNotFoundError(
                f"Minutes not found for meeting {meeting_id}: {minutes_path}"
            )

        _console(f"  Ingesting into database...")
        db_path = resolve_db_path(self._config.storage.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        session_factory = get_session_factory(f"sqlite:///{db_path}")
        session = session_factory()

        storage = StorageEngine(session)
        search = SearchEngine(session)
        ingester = MinutesIngester(storage, search)

        t0 = time.time()
        ingester.ingest(minutes_path)
        t_ingest = time.time() - t0

        self._logger.info("Ingested into database in %.1fs — db=%s", t_ingest, db_path)
        _console(f"  ✓ Ingested into database in {t_ingest:.1f}s", "green")
        _console(f"    DB: {db_path}")
        _console(f"    Full-text search index updated")

        # Apply prior-action closures from the generated minutes.
        try:
            self._apply_prior_action_updates(meeting_id, storage)
        except Exception as _exc:
            self._logger.warning("Prior-action closure step failed: %s", _exc)
        finally:
            session.close()

    def _apply_prior_action_updates(self, meeting_id: str, storage) -> None:
        """Close / re-status prior open action items acknowledged in this meeting.

        Reads the freshly written minutes JSON, looks at
        ``structured_data.prior_action_updates``, and calls
        ``StorageEngine.update_action_item_status`` for each. Logs each
        transition. Does not create new items — only mutates existing ones.
        """
        import json as _json

        gen_config = self._config.generation
        if not getattr(gen_config, "close_acknowledged_actions", True):
            return

        minutes_path = self._minutes_dir / f"{meeting_id}.json"
        if not minutes_path.exists():
            return

        try:
            with open(minutes_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            return

        structured = data.get("structured_data") or {}
        updates = structured.get("prior_action_updates") or []
        if not updates:
            return

        allowed = {"done", "in_progress", "cancelled"}
        applied = 0
        for u in updates:
            action_id = u.get("action_item_id")
            new_status = u.get("new_status")
            if not action_id or new_status not in allowed:
                continue
            ok = storage.update_action_item_status(action_id, new_status)
            if ok:
                applied += 1
                self._logger.info(
                    "Prior action %s → %s (from meeting %s)",
                    action_id, new_status, meeting_id[:8],
                )
        if applied:
            _console(
                f"  ✓ Closed/updated {applied} prior action item(s) based on this meeting",
                "green",
            )

    async def rediarize(self, meeting_id: str, regenerate: bool = True) -> None:
        """Re-run ONLY speaker diarization on existing audio, merge into existing transcript.

        Skips re-transcription (slow). Useful when diarization was broken at the
        time of original recording (missing HF_TOKEN, missing torchcodec, etc.)
        and you want to add speaker labels without re-running the full pipeline.

        If regenerate=True, also re-runs minutes generation and ingestion so the
        new speaker labels propagate through the database.
        """
        import json as _json
        from datetime import datetime, timezone

        from meeting_minutes.system1.diarize import DiarizationEngine
        from meeting_minutes.models import TranscriptSegment

        _console(f"\n{'='*60}", "bold")
        _console(f"  REDIARIZE — Meeting {meeting_id[:12]}...", "bold yellow")
        _console(f"{'='*60}\n")

        # Locate existing transcript JSON
        transcript_path = self._transcripts_dir / f"{meeting_id}.json"
        if not transcript_path.exists():
            raise FileNotFoundError(f"Transcript not found: {transcript_path}")

        # Locate audio file
        audio_path = self._recordings_dir / f"{meeting_id}.flac"
        if not audio_path.exists():
            for ext in (".wav", ".mp3", ".m4a", ".ogg"):
                candidate = self._recordings_dir / f"{meeting_id}{ext}"
                if candidate.exists():
                    audio_path = candidate
                    break
            else:
                raise FileNotFoundError(
                    f"No audio file found for meeting {meeting_id}. "
                    f"Cannot re-diarize without source audio."
                )

        if not self._config.diarization.enabled:
            _console("  [yellow]Diarization is disabled in config — enable it first.[/yellow]")
            return

        if not os.environ.get("HF_TOKEN"):
            _console("  [yellow]HF_TOKEN not set — diarization will fail.[/yellow]")
            return

        # Run diarization
        _console(f"  Audio: {audio_path.name} ({audio_path.stat().st_size / (1024*1024):.1f} MB)")
        _console(f"  Running speaker diarization (this can take 30s-3min)...")
        diarize_engine = DiarizationEngine(self._config.diarization)
        t0 = time.time()
        diarization_result = await asyncio.get_event_loop().run_in_executor(
            None, diarize_engine.diarize, audio_path
        )
        diarization_result.meeting_id = meeting_id
        t_diarize = time.time() - t0
        n_speakers = len(set(s.speaker for s in diarization_result.segments))
        n_segments = len(diarization_result.segments)

        if n_segments == 0:
            _console(f"  [red]✗ Diarization returned 0 segments in {t_diarize:.1f}s[/red]")
            _console(f"  [dim]Check server.err for the underlying error.[/dim]")
            return

        _console(f"  [green]✓[/green] Diarization done in {t_diarize:.1f}s — {n_speakers} speakers, {n_segments} segments")

        # Apply user-provided speaker names (in order of first-speaking)
        notes_file = self._data_dir / "notes" / f"{meeting_id}.json"
        if notes_file.exists():
            try:
                _notes = _json.loads(notes_file.read_text())
                _user_names = _notes.get("speakers") or []
                if isinstance(_user_names, str):
                    _user_names = [n.strip() for n in _user_names.split(",") if n.strip()]
                if _user_names:
                    mapping = DiarizationEngine.apply_speaker_names(diarization_result, _user_names)
                    if mapping:
                        _console(f"  [green]✓[/green] Mapped speakers: {', '.join(f'{k}→{v}' for k,v in mapping.items())}")
            except Exception as exc:
                _console(f"  [yellow]Could not apply user speaker names: {exc}[/yellow]")

        # SPK-1: recompute suggestions on re-diarization.
        spk1_suggestions: dict[str, dict] = {}
        try:
            spk1_suggestions = self._spk1_process(
                meeting_id=meeting_id,
                diarize_engine=diarize_engine,
                diarization_result=diarization_result,
            )
        except Exception as spk_exc:
            self._logger.warning("SPK-1 processing failed during rediarize: %s", spk_exc)

        # Load existing transcript JSON
        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript_data = _json.load(f)

        # Merge speakers into existing segments
        old_segments = transcript_data.get("transcript", {}).get("segments", [])
        segment_objs = [TranscriptSegment(**s) for s in old_segments]
        merged = DiarizationEngine.merge_transcript_with_diarization(
            segment_objs, diarization_result
        )

        # Build new speaker list — attach SPK-1 suggestions if present.
        seen_labels: set[str] = set()
        new_speakers = []
        for d_seg in diarization_result.segments:
            if d_seg.speaker not in seen_labels:
                seen_labels.add(d_seg.speaker)
                sugg = spk1_suggestions.get(d_seg.speaker, {}) if spk1_suggestions else {}
                new_speakers.append({
                    "label": d_seg.speaker,
                    "name": None,
                    "email": None,
                    "confidence": 0.0,
                    "suggested_person_id": sugg.get("suggested_person_id"),
                    "suggested_name": sugg.get("suggested_name"),
                    "suggestion_score": float(sugg.get("suggestion_score", 0.0)),
                    "suggestion_tier": sugg.get("suggestion_tier"),
                })

        # Update the transcript JSON in place
        transcript_data["speakers"] = new_speakers
        transcript_data["transcript"]["segments"] = [s.model_dump() for s in merged]
        transcript_data["processing"] = transcript_data.get("processing", {})
        transcript_data["processing"]["rediarized_at"] = datetime.now(timezone.utc).isoformat()

        with open(transcript_path, "w", encoding="utf-8") as f:
            _json.dump(transcript_data, f, indent=2, default=str)
        _console(f"  [green]✓[/green] Transcript updated with speaker labels: {transcript_path.name}")

        if regenerate:
            _console(f"\n  [bold]Re-running minutes generation with new speaker labels...[/bold]")
            await self.reprocess(meeting_id)

    async def resume_from(
        self,
        meeting_id: str,
        from_stage: Stage | None = None,
    ) -> None:
        """Resume the pipeline from the first non-succeeded stage (or explicit ``from_stage``).

        Stages already in ``succeeded`` are skipped. Runs forward through
        INGEST / EMBED / EXPORT. CAPTURE is a no-op here (audio is either on
        disk or it isn't). TRANSCRIBE+DIARIZE are covered by run_transcription.
        """
        if from_stage is None:
            session = self._state_session()
            try:
                from_stage = _state_next_stage(session, meeting_id)
            finally:
                session.close()
        if from_stage is None:
            _console(f"  All stages already succeeded for {meeting_id[:12]}", "dim")
            return

        # Snapshot current stage states to decide what to skip.
        session = self._state_session()
        try:
            states = {s.stage: s for s in get_stages(session, meeting_id)}
        finally:
            session.close()

        _console(f"\n{'='*60}", "bold")
        _console(f"  RESUME — Meeting {meeting_id[:12]} from {from_stage.value}", "bold yellow")
        _console(f"{'='*60}\n")

        order = Stage.ordered()
        start_idx = order.index(from_stage)

        for stage in order[start_idx:]:
            state = states.get(stage)
            if state and state.status == Status.SUCCEEDED:
                _console(f"  ✓ Skipping {stage.value} (already succeeded)", "dim")
                continue

            if stage == Stage.CAPTURE:
                # Audio capture can't be resumed programmatically; mark skipped
                # so downstream stages aren't blocked by a missing row.
                _console(f"  Skipping CAPTURE stage (manual step)", "dim")
                continue
            if stage in (Stage.TRANSCRIBE, Stage.DIARIZE):
                with self._track_stage(meeting_id, Stage.TRANSCRIBE) as ctx:
                    path = await self.run_transcription(meeting_id)
                    ctx.artifact_path = str(path)
                with self._track_stage(meeting_id, Stage.DIARIZE) as ctx:
                    ctx.artifact_path = str(path)
                continue
            if stage == Stage.GENERATE:
                with self._track_stage(meeting_id, Stage.GENERATE) as ctx:
                    path = await self.run_generation(meeting_id)
                    ctx.artifact_path = str(path)
                continue
            if stage == Stage.INGEST:
                with self._track_stage(meeting_id, Stage.INGEST):
                    await self.run_ingestion(meeting_id)
                continue
            if stage == Stage.EMBED:
                try:
                    with self._track_stage(meeting_id, Stage.EMBED):
                        await asyncio.get_event_loop().run_in_executor(
                            None, self._embed_meeting, meeting_id
                        )
                except Exception as exc:
                    self._logger.warning("Embedding failed during resume for %s: %s", meeting_id, exc)
                continue
            if stage == Stage.EXPORT:
                try:
                    with self._track_stage(meeting_id, Stage.EXPORT):
                        self._export_to_obsidian_from_file(meeting_id)
                except Exception as exc:
                    self._logger.warning("Obsidian export failed during resume: %s", exc)
                continue

        _console(f"\n{'='*60}", "bold")
        _console(f"  RESUME COMPLETE — {meeting_id[:12]}", "bold green")
        _console(f"{'='*60}\n")

    async def reprocess(self, meeting_id: str) -> None:
        """Re-run System 2 and System 3 for an existing meeting."""
        _console(f"\n{'='*60}", "bold")
        _console(f"  REPROCESS — Meeting {meeting_id[:12]}...", "bold yellow")
        _console(f"{'='*60}\n")

        # Delete old minutes if they exist
        minutes_path = self._minutes_dir / f"{meeting_id}.json"
        if minutes_path.exists():
            minutes_path.unlink()
            _console(f"  Deleted old minutes JSON")
        md_path = self._minutes_dir / f"{meeting_id}.md"
        if md_path.exists():
            md_path.unlink()
            _console(f"  Deleted old minutes Markdown")

        t0 = time.time()
        _console(f"\n[1/2] Re-generating minutes...", "bold cyan")
        await self.run_generation(meeting_id)
        _console(f"\n[2/2] Re-ingesting into database...", "bold cyan")
        await self.run_ingestion(meeting_id)

        elapsed = time.time() - t0
        _console(f"\n{'='*60}", "bold")
        _console(f"  REPROCESS COMPLETE — {elapsed:.1f}s", "bold green")
        _console(f"{'='*60}\n")

    def _embed_meeting(self, meeting_id: str) -> None:
        """Generate and store embeddings for a single meeting."""
        from meeting_minutes.embeddings import EmbeddingEngine
        from meeting_minutes.system3.db import get_session_factory

        db_path = resolve_db_path(self._config.storage.sqlite_path)
        session_factory = get_session_factory(f"sqlite:///{db_path}")
        session = session_factory()

        try:
            engine = EmbeddingEngine(self._config)
            count = engine.index_meeting(meeting_id, session, self._data_dir)
            if count > 0:
                self._logger.info("Indexed %d chunks for meeting %s", count, meeting_id)
                _console(f"  ✓ Indexed {count} chunks for semantic search", "green")
            else:
                _console(f"  ⚠ No chunks to index", "dim")
        except Exception as exc:
            self._logger.warning("Embedding failed: %s", exc)
            raise
        finally:
            session.close()

    def _detect_series(self) -> None:
        """Best-effort recurring-meeting detection after pipeline completion."""
        from meeting_minutes.system3.db import get_session_factory
        from meeting_minutes.system3.series import detect_and_upsert

        db_path = resolve_db_path(self._config.storage.sqlite_path)
        session_factory = get_session_factory(f"sqlite:///{db_path}")
        session = session_factory()
        try:
            summary = detect_and_upsert(session)
            if summary.created or summary.updated:
                self._logger.info(
                    "REC-1 detection: created=%d updated=%d unchanged=%d",
                    len(summary.created), len(summary.updated), len(summary.unchanged),
                )
        finally:
            session.close()

    def _maybe_backup(self) -> None:
        """Create a backup if the last one is older than the configured interval."""
        if not self._config.backup.enabled:
            return

        from meeting_minutes.backup import backup_database, rotate_backups

        backup_dir = Path(self._config.backup.backup_dir)

        # Check last backup time
        if backup_dir.exists():
            backups = sorted(backup_dir.glob("meetings_*.db"), reverse=True)
            if backups:
                last_backup_time = backups[0].stat().st_mtime
                hours_since = (time.time() - last_backup_time) / 3600
                if hours_since < self._config.backup.interval_hours:
                    return

        db_path = resolve_db_path(self._config.storage.sqlite_path)
        if db_path.exists():
            try:
                backup_file = backup_database(db_path, backup_dir)
                deleted = rotate_backups(backup_dir)
                _console(f"  \u2713 Database backed up: {backup_file.name}", "green")
                if deleted:
                    _console(f"    Rotated {deleted} old backup(s)", "dim")
            except Exception as exc:
                _console(f"  \u26a0 Backup failed: {exc}", "yellow")

    def _export_to_obsidian_from_file(self, meeting_id: str) -> None:
        """Export meeting minutes to Obsidian vault from the minutes JSON file."""
        if not self._config.obsidian.enabled or not self._config.obsidian.vault_path:
            return

        import json as _json

        from meeting_minutes.obsidian import export_to_obsidian

        vault_path = Path(self._config.obsidian.vault_path).expanduser()
        minutes_path = self._minutes_dir / f"{meeting_id}.json"
        md_path = self._minutes_dir / f"{meeting_id}.md"

        if not minutes_path.exists():
            return

        try:
            with open(minutes_path, "r", encoding="utf-8") as f:
                data = _json.load(f)

            metadata = data.get("metadata", {})
            md_content = ""
            if md_path.exists():
                md_content = md_path.read_text(encoding="utf-8")
            elif data.get("minutes_markdown"):
                md_content = data["minutes_markdown"]

            filepath = export_to_obsidian(
                vault_path=vault_path,
                title=metadata.get("title", f"Meeting {meeting_id[:8]}"),
                date=metadata.get("date", ""),
                meeting_type=data.get("meeting_type", "other"),
                attendees=metadata.get("attendees", []),
                minutes_markdown=md_content,
                summary=data.get("summary", ""),
                action_items=data.get("action_items", []),
                decisions=data.get("decisions", []),
                key_topics=data.get("key_topics", []),
                meeting_id=meeting_id,
            )
            _console(f"  \u2713 Exported to Obsidian: {filepath.name}", "green")
        except Exception as e:
            _console(f"  \u26a0 Obsidian export failed: {e}", "yellow")

    def start_watcher(self) -> None:
        """Start filesystem watcher for automatic mode (watchdog)."""
        if self._config.pipeline.mode != "automatic":
            return

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileCreatedEvent

            transcripts_dir = self._transcripts_dir
            transcripts_dir.mkdir(parents=True, exist_ok=True)

            class TranscriptHandler(FileSystemEventHandler):
                def __init__(self, orchestrator):
                    self._orchestrator = orchestrator

                def on_created(self, event):
                    if not event.is_directory and event.src_path.endswith(".json"):
                        path = Path(event.src_path)
                        meeting_id = path.stem
                        asyncio.create_task(
                            self._orchestrator.run_generation(meeting_id)
                        )

            observer = Observer()
            observer.schedule(
                TranscriptHandler(self),
                str(transcripts_dir),
                recursive=False,
            )
            observer.start()
            self._logger.info(f"Watching {transcripts_dir} for new transcripts")
        except ImportError:
            self._logger.warning("watchdog not installed, automatic mode unavailable")
