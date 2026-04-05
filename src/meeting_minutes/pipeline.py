"""Pipeline orchestrator — coordinates Systems 1, 2, and 3."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from meeting_minutes.config import AppConfig
from meeting_minutes.logging import get_logger


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
        transcript_path = await self._retry_async(
            self.run_transcription, meeting_id,
            max_retries=2, base_delay=5, step_name="Transcription",
        )

        _console("\n[2/3] Minutes generation...", "bold cyan")
        self._logger.info("[2/3] Minutes generation for %s", meeting_id)
        minutes_path = await self._retry_async(
            self.run_generation, meeting_id,
            max_retries=3, base_delay=3, step_name="Generation",
        )

        _console("\n[3/3] Database ingestion...", "bold cyan")
        self._logger.info("[3/3] Database ingestion for %s", meeting_id)
        await self._retry_async(
            self.run_ingestion, meeting_id,
            max_retries=1, base_delay=2, step_name="Ingestion",
        )

        # Post-processing: backup and Obsidian export
        self._maybe_backup()
        self._export_to_obsidian_from_file(meeting_id)

        # Retention cleanup
        from meeting_minutes.retention import enforce_retention
        enforce_retention(self._config)

        elapsed = time.time() - pipeline_start
        self._logger.info("Pipeline complete — %s — %.1fs total", meeting_id, elapsed)
        _console(f"\n{'='*60}", "bold")
        _console(f"  PIPELINE COMPLETE — {elapsed:.1f}s total", "bold green")
        _console(f"  Meeting ID: {meeting_id}", "dim")
        _console(f"{'='*60}\n")

        return meeting_id

    async def run_transcription(self, meeting_id: str) -> Path:
        """Run System 1: transcribe audio → produce transcript JSON."""
        from meeting_minutes.system1.output import TranscriptJSONWriter
        from meeting_minutes.system1.transcribe import TranscriptionEngine
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
        engine = TranscriptionEngine(self._config.transcription)
        model_name = self._config.transcription.whisper_model
        lang = self._config.transcription.language
        _console(f"  Whisper model: {model_name} | Language: {lang}")
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
        if self._config.diarization.enabled:
            _console(f"  Speaker diarization...", "yellow")
            diarize_engine = DiarizationEngine(self._config.diarization)
            try:
                t0 = time.time()
                diarization_result = await asyncio.get_event_loop().run_in_executor(
                    None, diarize_engine.diarize, audio_path
                )
                diarization_result.meeting_id = meeting_id
                t_diarize = time.time() - t0
                n_speakers = len(set(s.speaker for s in diarization_result.segments))
                _console(f"  ✓ Diarization done in {t_diarize:.1f}s — {n_speakers} speaker(s) detected", "green")
            except Exception as exc:
                self._logger.warning("Diarization failed: %s — continuing without", exc)
                _console(f"  ⚠ Diarization failed: {exc} — continuing without", "yellow")
        else:
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

        # Try structured generation first
        try:
            _console(f"  Trying structured generation (tool_use)...", "yellow")
            system_prompt, user_prompt = prompt_engine.render_structured(template, context, enhanced_transcript)
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

            # N11: Meeting type refinement — check if LLM suggests a different type
            if hasattr(structured, 'meeting_type_suggestion') and structured.meeting_type_suggestion:
                suggested = structured.meeting_type_suggestion
                if suggested != tj.meeting_type and suggested != "other":
                    self._logger.info(
                        "Meeting type refinement: classified as '%s' but content suggests '%s'",
                        tj.meeting_type, suggested,
                    )
                    _console(
                        f"  ℹ Type refinement: classified as '{tj.meeting_type}' but content suggests '{suggested}'",
                        "dim",
                    )
                    # Update the meeting type for storage
                    context.meeting_type = suggested

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
            full_prompt = prompt_engine.render(template, context, enhanced_transcript)
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
        db_path = Path(self._config.storage.sqlite_path).expanduser()
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

        db_path = Path(self._config.storage.sqlite_path).expanduser()
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
