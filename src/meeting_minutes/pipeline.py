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
    # Public API
    # -----------------------------------------------------------------------

    async def run_full_pipeline(self, meeting_id: str | None = None) -> str:
        """Run the full pipeline: record → transcribe → generate → ingest."""
        if meeting_id is None:
            meeting_id = str(uuid.uuid4())

        pipeline_start = time.time()
        _console(f"\n{'='*60}", "bold")
        _console(f"  PIPELINE START — Meeting {meeting_id[:12]}...", "bold green")
        _console(f"{'='*60}\n")

        _console("[1/3] Transcription...", "bold cyan")
        transcript_path = await self.run_transcription(meeting_id)

        _console("\n[2/3] Minutes generation...", "bold cyan")
        minutes_path = await self.run_generation(meeting_id)

        _console("\n[3/3] Database ingestion...", "bold cyan")
        await self.run_ingestion(meeting_id)

        elapsed = time.time() - pipeline_start
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
        _console(f"    Meeting type: {tj.meeting_type} (confidence: {tj.meeting_type_confidence:.2f})")
        _console(f"    Speakers: {len(tj.speakers)} | Transcript length: {len(transcript_data.full_text):,} chars")

        # Route to template
        gen_config = self._config.generation
        templates_dir = Path(gen_config.templates_dir)
        if not templates_dir.is_absolute():
            templates_dir = Path(__file__).parent.parent.parent / gen_config.templates_dir

        router = PromptRouter(gen_config, templates_dir)
        template = router.select_template(
            tj.meeting_type,
            tj.meeting_type_confidence,
        )
        _console(f"  Template selected: {template.name}")

        # Build context
        attendees = [s.name or s.label for s in tj.speakers]
        context = MeetingContext(
            meeting_id=meeting_id,
            title=f"Meeting {meeting_id[:8]}",
            date=tj.metadata.timestamp_start.strftime("%Y-%m-%d"),
            duration=f"{int(tj.metadata.duration_seconds // 60)} minutes",
            attendees=attendees,
            meeting_type=tj.meeting_type,
        )
        _console(f"  Context: {context.date} | {context.duration} | {len(attendees)} attendees")

        # Render prompt
        prompt_engine = PromptTemplateEngine(templates_dir)
        full_prompt = prompt_engine.render(template, context, transcript_data.full_text)
        _console(f"  Prompt rendered: {len(full_prompt):,} chars")

        # Generate with LLM
        provider = gen_config.llm.primary_provider
        model = gen_config.llm.model
        _console(f"  Calling LLM: {provider} / {model}...", "yellow")

        t0 = time.time()
        llm_client = LLMClient(gen_config.llm)
        llm_response = await llm_client.generate(full_prompt, system_prompt=template.system_prompt)
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

        # Use LLM-generated title if available, fall back to context title
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
