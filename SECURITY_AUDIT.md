# Security Audit: MeetingMinutesTaker

**Date:** 2026-05-17
**Scope:** Data theft vectors, confidential information exposure, LLM data privacy

---

## Executive Summary

This application processes highly sensitive data: full meeting transcripts, attendee PII, voice biometrics, decisions, and action items. The current security posture has **critical gaps** that could allow data theft by anyone with local network access. The most urgent issues are: (1) zero authentication on the REST API, (2) unencrypted database at rest, and (3) full meeting transcripts sent to cloud LLM providers without PII scrubbing.

**Risk Rating:** CRITICAL

---

## 1. Database Data Theft

### 1.1 No Authentication on API Endpoints (CRITICAL)

**Impact:** Anyone who can reach the API can read, modify, or delete all meeting data.

Every REST endpoint is publicly accessible with no authentication:
- `GET /api/meetings` returns all meetings with transcripts, attendees, decisions
- `DELETE /api/meetings/{id}` deletes any meeting
- `POST /api/backups` creates full database copies
- `PATCH /api/config` modifies application configuration
- `PUT /api/config/secrets/{name}` overwrites API keys stored in `.env`

**Files:**
- [deps.py](src/meeting_minutes/api/deps.py) - no auth middleware or dependency
- [main.py](src/meeting_minutes/api/main.py:79) - CORS middleware but no auth middleware
- [meetings.py](src/meeting_minutes/api/routes/meetings.py) - all endpoints unauthenticated

**Attack scenario:** If the API is bound to `0.0.0.0` (via `--host 0.0.0.0` or config), any device on the network can exfiltrate the entire database through the API. Even on localhost, any local process or browser tab (via CORS misconfiguration) could access the data.

**Remediation:**
- Add authentication middleware (JWT or session-based) to all endpoints
- Require API key validation as a minimum for localhost deployments
- Audit and restrict the CORS origin list; reject `*` configurations
- Add authorization checks so destructive operations require elevated privileges

### 1.2 Unencrypted SQLite Database (CRITICAL)

**Impact:** Anyone with filesystem access can read all meeting data directly from the database file.

The SQLite database at `~/MeetingMinutesTaker/db/meetings.db` stores all sensitive data in plaintext:
- Full meeting transcripts (complete conversations)
- Attendee names and email addresses
- Voice embeddings (biometric data)
- Decisions, action items, and meeting summaries
- Chat conversation history with the AI assistant

Encryption exists ([encryption.py](src/meeting_minutes/encryption.py)) but is **disabled by default** and only covers exported JSON files, not the database itself.

**Files:**
- [config.py:181](src/meeting_minutes/config.py:181) - `encryption_enabled: bool = False`
- [models.py](src/meeting_minutes/models.py) - schema showing all plaintext columns
- [db.py](src/meeting_minutes/system3/db.py) - no SQLCipher or column-level encryption

**Remediation:**
- Use SQLCipher for transparent database encryption at rest
- Set database file permissions to `0600` (owner-only) immediately after creation
- Enable encryption by default and generate a key on first run
- Add column-level encryption for the most sensitive fields (transcripts, voice embeddings)

### 1.3 Unprotected Database File Permissions (HIGH)

**Impact:** Other users on a shared system can read the database file.

No `chmod()` is called after creating the database file or its parent directory. SQLite creates files with default OS permissions (typically `0644`, world-readable).

**Files:**
- [main.py:27](src/meeting_minutes/api/main.py:27) - `db_path.parent.mkdir(parents=True, exist_ok=True)` with no chmod
- [db.py](src/meeting_minutes/system3/db.py) - `create_engine()` with no permission hardening

**Remediation:**
```python
db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
# After engine creation:
db_path.chmod(0o600)
```

### 1.4 Backup Files Unencrypted and Unauthenticated (CRITICAL)

**Impact:** Full database copies are created without encryption and accessible without authentication.

**Files:**
- [backup.py](src/meeting_minutes/backup.py) - creates backups with default permissions, no encryption
- [routes/backup.py](src/meeting_minutes/api/routes/backup.py) - no auth on list/create endpoints

**Remediation:**
- Encrypt backups using the configured encryption key
- Set backup file permissions to `0600`
- Add authentication to backup API endpoints
- Rate-limit backup creation to prevent DoS

### 1.5 SQL Injection in Search (MODERATE)

**Impact:** Potential SQL injection via string interpolation in the FTS search query.

**File:** [search.py:131](src/meeting_minutes/system3/search.py:131)
```python
id_list = ",".join(f"'{mid}'" for mid in fts_meeting_ids)
conditions.append(f"m.meeting_id IN ({id_list})")
```

Meeting IDs are server-generated UUIDs, limiting practical exploitation. However, this violates secure coding practices.

**Remediation:** Use parameterized queries with SQLAlchemy's `bindparam` or `in_()` operator for the IN clause.

### 1.6 Insecure File Deletion (HIGH)

**Impact:** Deleted audio files and transcripts can be recovered with forensic tools.

**File:** [retention.py:155](src/meeting_minutes/retention.py:155) - uses `f.unlink()` (simple delete, no secure wipe)

**Remediation:**
- Overwrite files with random data before unlinking for sensitive content
- Or use cryptographic erasure (encrypt files at rest, destroy key on deletion)
- Document the security properties of the deletion mechanism

---

## 2. Confidential Information Exposure

### 2.1 Configuration & API Key Manipulation (CRITICAL)

**Impact:** An attacker can overwrite API keys, redirect data to attacker-controlled LLM endpoints, or change the data directory.

**File:** [routes/config.py](src/meeting_minutes/api/routes/config.py)
- `PATCH /api/config` modifies any configuration field
- `PUT /api/config/secrets/{name}` writes API keys to `.env` with no authentication
- `GET /api/config/secrets/{name}` reveals which API keys are configured
- `GET /api/config/hardware` reveals GPU, RAM, CPU, platform details

Writable secrets include: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `HF_TOKEN`, `PYANNOTEAI_API_KEY`

**Attack scenario:** Attacker replaces `ANTHROPIC_API_KEY` with their own key. All subsequent LLM calls go through the attacker's account, allowing them to log all meeting transcripts sent for processing.

**Remediation:**
- Require strong authentication for all config/secret endpoints
- Add an audit log for configuration changes
- Require re-authentication or a confirmation step for secret writes
- Never expose secret existence checks without authentication

### 2.2 Recording Control Without Authentication (CRITICAL)

**Impact:** A remote attacker can start and stop audio recordings on the user's machine.

**File:** [routes/recording.py](src/meeting_minutes/api/routes/recording.py)
- `POST /api/recording/start` - start recording audio
- `POST /api/recording/stop` - stop recording and trigger pipeline
- Recording state stored in world-writable `/tmp/mm_recording_state.json`

**Remediation:**
- Add authentication to recording endpoints
- Store recording state in the application data directory with restricted permissions
- Add user confirmation for recording start/stop via the UI

### 2.3 Information Disclosure via Health/Doctor Endpoints (MEDIUM)

**Impact:** System configuration, paths, and provider status are exposed.

**Files:**
- [routes/health.py](src/meeting_minutes/api/routes/health.py) - database and service connectivity
- [routes/doctor.py](src/meeting_minutes/api/routes/doctor.py) - diagnostic checks revealing system state
- [routes/config.py:143](src/meeting_minutes/api/routes/config.py:143) - `GET /api/config/resolved-paths` reveals absolute filesystem paths

**Remediation:**
- Add authentication to these endpoints
- Limit information exposure to authenticated admin users
- Redact absolute paths and internal details from responses

### 2.4 MCP Server Full Database Access (HIGH)

**Impact:** Any MCP client gets complete read-write access to the meeting database.

**Files:**
- [mcp_server/tools.py](src/meeting_minutes/mcp_server/tools.py) - 11 tools including `get_transcript`, `list_meetings`, `update_action_item`
- Transport: stdio (local only by default), but no authentication if exposed over network

**Remediation:**
- Add authentication for MCP transport
- Implement per-tool authorization
- Log all MCP tool invocations

### 2.5 Encryption Key Can Be Stored in config.yaml (MODERATE)

**Impact:** Encryption key stored in a file that may be committed to version control or shared.

**File:** [config.py:183](src/meeting_minutes/config.py:183) - `encryption_key: str = ""` with comment to prefer env var

**Remediation:**
- Remove the `encryption_key` field from the config model
- Require the `MM_ENCRYPTION_KEY` environment variable exclusively
- Warn or error if the key is found in the config file

---

## 3. LLM Data Privacy

### 3.1 Full Transcripts Sent to Cloud LLMs Without PII Scrubbing (CRITICAL)

**Impact:** Complete meeting conversations including names, decisions, and sensitive discussions are transmitted to third-party cloud providers.

**Data sent to LLM providers:**

| Feature | Data Sent | Provider |
|---------|-----------|----------|
| Minutes generation | Full transcript + all attendee names + organizer + title | Anthropic/OpenAI/OpenRouter |
| Meeting classification | 4,000-char transcript excerpt + title + speaker count | Anthropic (Claude Haiku) |
| Chat/RAG queries | User query + retrieved meeting excerpts + conversation history | Configured provider |
| Brief summary | Attendee names + commitment/decision counts | Configured provider |
| Attachment summarization | Full extracted text (up to 100KB) + title + caption | Configured provider |

**Files:**
- [prompts.py:79-103](src/meeting_minutes/system2/prompts.py:79) - template includes `transcript_text`, `attendees`, `organizer`
- [ingest.py:48-70](src/meeting_minutes/system2/ingest.py:48) - speaker names mapped before LLM call
- [chat.py:120-158](src/meeting_minutes/chat.py:120) - meeting excerpts + conversation history sent
- [summarizer.py:163-209](src/meeting_minutes/attachments/summarizer.py:163) - full attachment text sent

No PII redaction, name masking, or data minimization is performed before any LLM call.

**Remediation:**
- Implement a PII scrubbing layer that runs before any LLM call
- Replace real names with pseudonyms (SPEAKER_1, SPEAKER_2) before sending to cloud
- Re-map pseudonyms back to real names in the response
- Add a config option for "local-only mode" that blocks all cloud LLM calls
- Document exactly what data is sent to which provider

### 3.2 Audio Files Uploaded to pyannoteAI Cloud (HIGH)

**Impact:** Complete audio recordings of meetings are uploaded to third-party servers.

**File:** [pyannote_ai.py:128](src/meeting_minutes/system1/diarization_backends/pyannote_ai.py:128)
```python
media_url = client.upload(str(audio_path))
```

Full audio files are uploaded to pyannoteAI's cloud infrastructure for speaker diarization.

**Remediation:**
- Default to local diarization (pyannote_local) instead of cloud
- Require explicit user opt-in for cloud diarization
- Display a clear warning when cloud diarization is selected
- Document the data residency implications

### 3.3 No Data Residency Controls (MODERATE)

**Impact:** Users have no way to control where their data is processed geographically.

Data may be sent to:
- **Anthropic** (US-based)
- **OpenAI** (US-based)
- **OpenRouter** (proxy/aggregator, unclear data residency)
- **pyannoteAI** (unclear data residency)

**Remediation:**
- Add a `data_residency` config option (e.g., `local_only`, `eu`, `us`)
- Document which providers operate in which jurisdictions
- Default to local processing where available

### 3.4 API Keys Stored in Plaintext (MODERATE)

**Impact:** API keys for LLM providers are stored in plaintext `.env` files.

**Files:**
- [secrets.py:112](src/meeting_minutes/api/secrets.py:112) - writes `NAME="value"` to `.env`
- [env.py:33-50](src/meeting_minutes/env.py:33) - loads keys into `os.environ` at startup

Positive: `.env` file permissions are set to `0600` after write. But keys are still plaintext on disk and in process memory.

**Remediation:**
- Consider OS keychain integration (macOS Keychain, Linux secret-service)
- Document the security properties of the current storage mechanism
- Warn users not to commit `.env` files

### 3.5 No LLM Call Audit Trail (MODERATE)

**Impact:** No record of what data was sent to which LLM provider and when.

Provider and model names are logged on failure, but successful calls and the data payloads are not audited.

**Remediation:**
- Log metadata for every LLM call: timestamp, provider, model, data size, meeting ID
- Do NOT log the actual prompt/response content (that would create another copy of sensitive data)
- Provide a way for users to review what data has been sent to cloud providers

### 3.6 Transcription Is Local-Only (POSITIVE)

Transcription uses `faster-whisper` locally. No audio is sent to external services for transcription. This is a good security design.

**File:** [transcribe.py](src/meeting_minutes/system1/transcribe.py) - CTranslate2-based local inference

### 3.7 Embeddings Are Local-Only (POSITIVE)

Embeddings use `sentence-transformers` (`BAAI/bge-small-en-v1.5`) locally. No meeting text is sent externally for embedding generation.

**File:** [embeddings.py:22-49](src/meeting_minutes/embeddings.py:22)

---

## 4. Prioritized Remediation Plan

### Immediate (Week 1) - CRITICAL items

| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 1 | Add authentication middleware to all API endpoints | Medium | Blocks all unauthenticated access |
| 2 | Set database file permissions to `0600` after creation | Low | Prevents local user data theft |
| 3 | Set backup file/directory permissions to `0600`/`0700` | Low | Protects backup copies |
| 4 | Move recording state out of `/tmp` to app data dir | Low | Prevents local tampering |
| 5 | Require auth for config/secrets endpoints | Medium | Prevents API key hijacking |

### Short-term (Weeks 2-4) - HIGH items

| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 6 | Implement PII scrubbing before LLM calls | High | Protects names/PII sent to cloud |
| 7 | Enable database encryption (SQLCipher) by default | Medium | Protects data at rest |
| 8 | ~~Default to local diarization over cloud~~ | ~~Low~~ | ✅ Already defaults to local (`pyannote`) |
| 9 | ~~Encrypt backup files~~ | ~~Medium~~ | ✅ Fernet encryption when `encryption_enabled` is true |
| 10 | ~~Fix SQL string interpolation in search~~ | ~~Low~~ | ✅ Parameterized bind params |
| 11 | ~~Add global rate limiting to all endpoints~~ | ~~Medium~~ | ✅ `RateLimitMiddleware` (opt-in via `api.rate_limit_rpm`) |

### Medium-term (Months 2-3) - MODERATE items

| # | Finding | Effort | Impact |
|---|---------|--------|--------|
| 12 | Add LLM call audit logging | Medium | Transparency on cloud data flow |
| 13 | Add data residency configuration | Medium | Compliance with data regulations |
| 14 | Remove encryption_key from config model | Low | Prevents key in version control |
| 15 | Implement secure file deletion | Medium | Prevents forensic data recovery |
| 16 | OS keychain integration for API keys | High | Stronger key storage |
| 17 | Add "local-only" processing mode | Medium | Zero cloud data transmission |
| 18 | Document data flow to all external providers | Low | User transparency |

---

## 5. Data Flow Diagram

```
User's Machine
+------------------------------------------------------------------+
|                                                                  |
|  Audio Input ──> [Transcribe (LOCAL)] ──> Transcript             |
|                        |                      |                  |
|                  [Diarize]                     |                  |
|                  (LOCAL or CLOUD*)             |                  |
|                        |                      |                  |
|                        v                      v                  |
|               Speaker-labeled          [LLM: Minutes Gen] ──────>|──> Anthropic/OpenAI/
|               Transcript                                         |    OpenRouter (CLOUD)
|                        |                      |                  |    - Full transcript
|                        v                      v                  |    - Attendee names
|                  +-----------+          +----------+             |    - Meeting metadata
|                  | SQLite DB |          | Minutes  |             |
|                  | (PLAIN-   |          | JSON     |             |
|                  |  TEXT)     |          +----------+             |
|                  +-----------+               |                   |
|                        |              [Embed (LOCAL)]            |
|                        |                     |                   |
|                        v                     v                   |
|                  +------------+       +-------------+            |
|                  | Backup     |       | Vector      |            |
|                  | (PLAINTEXT)|       | Embeddings  |            |
|                  +------------+       | (LOCAL)     |            |
|                                       +-------------+            |
|                        |                     |                   |
|                  [REST API]            [Chat/RAG] ──────────────>|──> Anthropic/OpenAI/
|                  NO AUTH!              Sends excerpts to LLM     |    OpenRouter (CLOUD)
|                                                                  |
|  * pyannoteAI cloud: full audio uploaded                         |
+------------------------------------------------------------------+
```

---

## 6. Compliance Considerations

The application stores and processes data that falls under multiple data protection regulations:

- **GDPR (EU):** Meeting transcripts with participant names, voice embeddings (biometric data under Art. 9), email addresses. Sending to US-based cloud providers requires adequate safeguards.
- **CCPA (California):** Personal information of meeting participants.
- **Voice biometric laws:** Several US states (Illinois BIPA, Texas CUBI, Washington) regulate voice biometric data. The `VoiceSampleORM` table stores speaker embeddings.

Current gaps:
- No data processing consent mechanism
- No data subject access request (DSAR) support
- No data portability export
- No purpose limitation enforcement
- Voice biometric data stored without explicit consent flow
