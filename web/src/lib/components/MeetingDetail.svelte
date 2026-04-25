<script>
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  import MeetingTypeBadge from './MeetingTypeBadge.svelte';
  import { MEETING_TYPE_GROUPS, MEETING_TYPE_MAP } from '../meetingTypes.js';
  import MarkdownRenderer from './MarkdownRenderer.svelte';
  import AudioPlayer from './AudioPlayer.svelte';
  import ActionItemRow from './ActionItemRow.svelte';
  import DecisionCard from './DecisionCard.svelte';
  import TagEditor from './TagEditor.svelte';
  import PersonAvatar from './PersonAvatar.svelte';
  import Skeleton from './Skeleton.svelte';
  import ConfirmModal from './ConfirmModal.svelte';
  import ExportMenu from './ExportMenu.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  /** @type {{ meetingId: string }} */
  let { meetingId, onDelete = null } = $props();

  let meeting = $state(null);
  let transcript = $state(null);
  let analytics = $state(null);
  let loading = $state(true);
  let activeTab = $state('minutes');
  let showDeleteModal = $state(false);
  let regenerating = $state(false);
  let showAllAttendees = $state(false);
  let currentAudioTime = $state(0);
  let audioPlayerRef = $state(null);
  let expandedTopics = $state(new Set());
  let showRawMarkdown = $state(false);
  let showSpeakerEditor = $state(false);
  let speakerEdits = $state({});          // { "SPEAKER_00": "Tom", ... }
  let speakerPersonIds = $state({});      // { "SPEAKER_00": "p-jon", ... }
  let savingSpeakers = $state(false);
  // SPK-1: per-cluster suggestion metadata loaded from the server.
  let speakerSuggestions = $state({});    // { "SPEAKER_00": {suggested_name, suggestion_tier, score, speech_seconds, suggested_person_id} }
  // Inline "create new person" form state, keyed by cluster id.
  let newPersonForms = $state({});        // { "SPEAKER_00": { open, name, email, saving } }
  // All known people, fetched once when the speaker editor opens. Used as
  // the autocomplete source for the rename inputs (<datalist>) and for the
  // case-insensitive name → person_id resolve so we can show "Will link to
  // <name>" feedback before the user even submits.
  let knownPeople = $state([]);           // [{ person_id, name, email }, ...]
  // REC-1: series this meeting belongs to, if any.
  let meetingSeries = $state(null);

  // External-notes tab state. The textarea is seeded from the server (so
  // the user sees their prior paste on reload) and we poll `/meetings/{id}`
  // while the background job is running so the status can flip to "ready"
  // or "error" without a manual refresh.
  let externalNotesDraft = $state('');
  let submittingExternalNotes = $state(false);
  let externalNotesPoller = null;

  // Meeting-type-change state. The dropdown sits beside the type badge in the
  // Minutes tab; on change we POST to the server (202) and start polling
  // /meetings/{id} so regen_status flips from "processing" to "ready".
  let editingType = $state(false);
  let pendingType = $state(null);            // value selected in the dropdown
  let submittingTypeChange = $state(false);
  let showTypeChangeModal = $state(false);

  // Title-edit state. Inline-editable header; on save we PATCH the meeting
  // and the server rewrites the on-disk minutes JSON/MD + Obsidian export.
  let editingTitle = $state(false);
  let pendingTitle = $state('');
  let savingTitle = $state(false);
  // Single poller for any in-flight regeneration (type change, speaker
  // rename, etc.). The server uses one regen_status field across all
  // triggers — only one regen runs at a time per meeting.
  let regenPoller = null;

  async function loadMeetingSeries(id) {
    try {
      const payload = await api.getMeetingSeries(id);
      meetingSeries = payload.series || null;
    } catch {
      meetingSeries = null;
    }
  }

  async function loadSpeakerSuggestions(id) {
    try {
      const payload = await api.getSpeakerSuggestions(id);
      const map = {};
      for (const s of payload.suggestions || []) {
        if (s.cluster_id) map[s.cluster_id] = s;
      }
      speakerSuggestions = map;
    } catch (e) {
      speakerSuggestions = {};
    }
  }

  // Synthetic cluster id used in the editor for transcript segments that
  // diarization didn't assign to any real cluster (speaker is null or empty
  // in the transcript JSON). On submit it's translated back to ``""`` for
  // the server, which apply_speaker_mapping recognizes as "rewrite all
  // segments with no speaker label."
  const UNKNOWN_CLUSTER = '__unknown__';

  async function openSpeakerEditor() {
    const segments = transcript?.segments || [];
    const realLabels = [...new Set(segments.map(s => s.speaker).filter(Boolean))];
    const hasUnassigned = segments.some(s => !s.speaker);
    // Append the synthetic Unknown row at the end so the user can see it
    // separately from real clusters. Only include it if there's actually
    // something to rename.
    const uniques = hasUnassigned ? [...realLabels, UNKNOWN_CLUSTER] : realLabels;
    const edits = {};
    const personIds = {};
    for (const label of uniques) {
      if (label === UNKNOWN_CLUSTER) {
        edits[label] = '';
        continue;
      }
      const sugg = speakerSuggestions[label] || {};
      // Prefill high/medium suggestions; leave unknown ones blank for the
      // user. If the label is already a human name (not SPEAKER_XX), keep it.
      if (!label.startsWith('SPEAKER_')) {
        edits[label] = label;
      } else if (sugg.suggested_name && (sugg.suggestion_tier === 'high' || sugg.suggestion_tier === 'medium')) {
        edits[label] = sugg.suggested_name;
        personIds[label] = sugg.suggested_person_id || '';
      } else {
        edits[label] = '';
      }
    }
    speakerEdits = edits;
    speakerPersonIds = personIds;
    newPersonForms = {};
    showSpeakerEditor = true;
    // Load people in the background for autocomplete. Don't block opening
    // the editor on this — the <datalist> is purely additive, the editor is
    // usable without it.
    try {
      const payload = await api.getPeople();
      knownPeople = (payload.items || []).map(p => ({
        person_id: p.person_id, name: p.name, email: p.email,
      }));
    } catch (e) {
      knownPeople = [];
    }
  }

  // Resolve a typed name against knownPeople (case-insensitive exact match).
  // Returns the matched person object or null. Used to show ambient feedback
  // ("Will link to <name>") and to fill speakerPersonIds before submit so the
  // server doesn't have to redo the lookup. The server *does* still resolve
  // independently — this is a UX hint, not a correctness gate.
  function resolveTypedName(name) {
    const trimmed = (name || '').trim().toLowerCase();
    if (!trimmed) return null;
    return knownPeople.find(p => (p.name || '').toLowerCase() === trimmed) || null;
  }

  function openNewPersonForm(label) {
    newPersonForms = {
      ...newPersonForms,
      [label]: { open: true, name: '', email: '', saving: false },
    };
  }

  function closeNewPersonForm(label) {
    const next = { ...newPersonForms };
    delete next[label];
    newPersonForms = next;
  }

  async function submitNewPerson(label) {
    const form = newPersonForms[label];
    if (!form || !form.name.trim()) {
      addToast('Name is required', 'warning');
      return;
    }
    newPersonForms = { ...newPersonForms, [label]: { ...form, saving: true } };
    try {
      const created = await api.createPerson({
        name: form.name.trim(),
        email: form.email.trim() || null,
      });
      speakerEdits = { ...speakerEdits, [label]: created.name };
      speakerPersonIds = { ...speakerPersonIds, [label]: created.person_id };
      closeNewPersonForm(label);
      addToast(`Created person: ${created.name}`, 'success');
    } catch (e) {
      addToast(`Could not create person: ${e.message}`, 'error');
      newPersonForms = { ...newPersonForms, [label]: { ...form, saving: false } };
    }
  }

  async function saveSpeakerEdits(regenerate = true) {
    savingSpeakers = true;
    try {
      const mapping = {};
      const personMapping = {};
      for (const [label, name] of Object.entries(speakerEdits)) {
        if (!name || !name.trim()) continue;
        // Translate the synthetic Unknown placeholder back to "" — the
        // backend treats it as "all null/empty-speaker segments".
        const submitLabel = label === UNKNOWN_CLUSTER ? '' : label;
        mapping[submitLabel] = name.trim();
      }
      // Best-effort prefill of person_mapping from local state. The server
      // resolves any clusters we miss by case-insensitive name match (or
      // creates a new Person on the fly), so this is a UX hint only.
      for (const [label, pid] of Object.entries(speakerPersonIds)) {
        const submitLabel = label === UNKNOWN_CLUSTER ? '' : label;
        if (pid && mapping[submitLabel]) personMapping[submitLabel] = pid;
      }
      if (Object.keys(mapping).length === 0) {
        addToast('No speaker names entered', 'warning');
        return;
      }
      const payload = { mapping, regenerate };
      if (Object.keys(personMapping).length > 0) {
        payload.person_mapping = personMapping;
      }
      const resp = await api.updateTranscriptSpeakers(meetingId, payload);
      const renamed = Object.keys(mapping).length;
      const confirmed = resp?.spk1?.confirmed || 0;
      const created = resp?.spk1?.created_persons || [];
      // Build a single toast that summarises what happened — rename count,
      // voice samples confirmed, and any newly-created persons.
      const parts = [`Renamed ${renamed} speaker(s)`];
      if (confirmed > 0) parts.push(`${confirmed} voice sample(s) saved`);
      if (created.length > 0) {
        parts.push(`created ${created.length} new person(s): ${created.map(p => p.name).join(', ')}`);
      }
      addToast(parts.join('; '), 'success');
      showSpeakerEditor = false;
      // Reload transcript right away so the new labels show even before the
      // async regen completes.
      await loadTranscript(meetingId);
      await loadSpeakerSuggestions(meetingId);
      if (regenerate) {
        // Server already scheduled the background regen — just refresh so
        // the regen pill shows "processing" and start polling.
        await loadMeeting(meetingId);
        startRegenPolling({
          successToast: 'Minutes regenerated with new speaker names',
          errorPrefix: 'Speaker rename regen failed',
        });
      }
    } catch (e) {
      addToast(`Failed to update speakers: ${e.message}`, 'error');
    } finally {
      savingSpeakers = false;
    }
  }

  // --- External notes -----------------------------------------------------
  //
  // The user pastes notes from Teams / Zoom / Meet / Otter etc. We ship the
  // raw text to the server; it archives the paste, kicks off a background
  // job (speaker rename + full regeneration), and returns 202 immediately.
  // We then poll the meeting every few seconds so the status pill updates
  // without the user having to refresh.

  function stopExternalNotesPolling() {
    if (externalNotesPoller) {
      clearInterval(externalNotesPoller);
      externalNotesPoller = null;
    }
  }

  function startExternalNotesPolling() {
    stopExternalNotesPolling();
    // 4s feels responsive without hammering the API; reprocess typically
    // takes 15-60s depending on transcript length + model.
    externalNotesPoller = setInterval(async () => {
      try {
        const raw = await api.getMeeting(meetingId);
        if (raw.external_notes_status !== 'processing') {
          stopExternalNotesPolling();
          // Full reload so the regenerated Minutes tab picks up new content.
          await loadMeeting(meetingId);
          await loadTranscript(meetingId);
          if (raw.external_notes_status === 'ready') {
            addToast('Minutes updated with external notes', 'success');
          } else if (raw.external_notes_status === 'error') {
            addToast(`External-notes update failed: ${raw.external_notes_error || 'unknown error'}`, 'error');
          }
        }
      } catch (e) {
        // Transient errors are fine — keep polling. A hard failure will
        // surface on the next full loadMeeting anyway.
      }
    }, 4000);
  }

  // --- Generic regen polling ----------------------------------------------
  //
  // The server uses a single ``regen_status`` field for any in-flight async
  // regeneration (meeting-type change, speaker rename, …). Only one regen
  // runs at a time per meeting, so one poller is enough. Caller passes
  // success/error toast strings so each trigger can speak its own language.

  function stopRegenPolling() {
    if (regenPoller) {
      clearInterval(regenPoller);
      regenPoller = null;
    }
  }

  function startRegenPolling({ successToast, errorPrefix } = {}) {
    stopRegenPolling();
    regenPoller = setInterval(async () => {
      try {
        const raw = await api.getMeeting(meetingId);
        if (raw.regen_status !== 'processing') {
          stopRegenPolling();
          await loadMeeting(meetingId);
          // Reload transcript too — speaker renames change segment labels.
          await loadTranscript(meetingId);
          if (raw.regen_status === 'ready') {
            addToast(successToast || 'Minutes regenerated', 'success');
          } else if (raw.regen_status === 'error') {
            addToast(
              `${errorPrefix || 'Regeneration failed'}: ${raw.regen_error || 'unknown error'}`,
              'error',
            );
          }
        }
      } catch (e) {
        // Transient errors are fine — keep polling.
      }
    }, 4000);
  }

  function openTypeEditor() {
    pendingType = meeting?.type || null;
    editingType = true;
  }

  function cancelTypeEdit() {
    editingType = false;
    pendingType = null;
  }

  function requestTypeChange() {
    if (!pendingType || pendingType === meeting?.type) return;
    showTypeChangeModal = true;
  }

  async function confirmTypeChange() {
    showTypeChangeModal = false;
    if (!pendingType || pendingType === meeting?.type) return;
    submittingTypeChange = true;
    try {
      await api.changeMeetingType(meetingId, pendingType);
      addToast('Regenerating minutes with new meeting type…', 'info');
      editingType = false;
      pendingType = null;
      // Refresh so the status pill shows "processing", then poll until done.
      await loadMeeting(meetingId);
      startRegenPolling({
        successToast: 'Minutes regenerated with new meeting type',
        errorPrefix: 'Type change failed',
      });
    } catch (e) {
      addToast(`Failed to change meeting type: ${e.message}`, 'error');
    } finally {
      submittingTypeChange = false;
    }
  }

  async function submitExternalNotes() {
    const text = externalNotesDraft.trim();
    if (!text) {
      addToast('Paste some notes first', 'warning');
      return;
    }
    submittingExternalNotes = true;
    try {
      await api.submitExternalNotes(meetingId, text);
      addToast('External notes saved — regenerating minutes in the background', 'info');
      // Refresh the meeting so the status pill shows "processing", then
      // poll until it flips.
      await loadMeeting(meetingId);
      startExternalNotesPolling();
    } catch (e) {
      addToast(`Failed to save external notes: ${e.message}`, 'error');
    } finally {
      submittingExternalNotes = false;
    }
  }

  function toggleTopic(idx) {
    const next = new Set(expandedTopics);
    if (next.has(idx)) next.delete(idx);
    else next.add(idx);
    expandedTopics = next;
  }

  function expandAllTopics() {
    expandedTopics = new Set(meeting?.discussion_points?.map((_, i) => i) || []);
  }

  function collapseAllTopics() {
    expandedTopics = new Set();
  }

  function sentimentBadgeClass(sentiment) {
    switch (sentiment) {
      case 'positive':
      case 'constructive':
        return 'bg-green-500/15 text-green-400 border-green-500/30';
      case 'negative':
      case 'tense':
        return 'bg-red-500/15 text-red-400 border-red-500/30';
      case 'mixed':
        return 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30';
      case 'neutral':
        return 'bg-blue-500/15 text-blue-400 border-blue-500/30';
      default:
        return 'bg-[var(--bg-surface-hover)] text-[var(--text-muted)] border-[var(--border-subtle)]';
    }
  }

  // Action-review state. Action items extracted from the transcript land as
  // "proposed" — the user accepts/rejects each before it joins the tracked
  // set. Counts drive the tab label, outcomes preview card, and review banner.
  const proposedActions = $derived(
    (meeting?.actions || []).filter(a => (a.proposal_state || 'confirmed') === 'proposed')
  );
  const confirmedActions = $derived(
    (meeting?.actions || []).filter(a => (a.proposal_state || 'confirmed') === 'confirmed')
  );
  const rejectedActions = $derived(
    (meeting?.actions || []).filter(a => (a.proposal_state || 'confirmed') === 'rejected')
  );
  let bulkReviewing = $state(false);

  async function bulkReview({ confirm = [], reject = [] } = {}) {
    if (!meeting?.meeting_id || (!confirm.length && !reject.length)) return;
    bulkReviewing = true;
    try {
      const result = await api.bulkReviewActionItems(meeting.meeting_id, { confirm, reject });
      meeting.actions = result.items || [];
      addToast({
        type: 'success',
        message: confirm.length && reject.length
          ? `Accepted ${confirm.length}, rejected ${reject.length}`
          : confirm.length ? `Accepted ${confirm.length} action${confirm.length === 1 ? '' : 's'}`
          : `Rejected ${reject.length} action${reject.length === 1 ? '' : 's'}`,
      });
    } catch (e) {
      addToast({ type: 'error', message: `Review failed: ${e.message}` });
    } finally {
      bulkReviewing = false;
    }
  }

  function acceptAllProposed() {
    bulkReview({ confirm: proposedActions.map(a => a.action_item_id || a.id) });
  }

  function rejectAllProposed() {
    bulkReview({ reject: proposedActions.map(a => a.action_item_id || a.id) });
  }

  // After a single-row update from ActionItemRow, refetch the meeting so
  // counts/outcomes/tab labels stay in sync. The row already mutated `item`
  // in place, but we also want to recompute derived counts and re-pull the
  // server-rendered markdown (since proposal_state changes re-render the
  // ## Action Items section server-side).
  async function onActionRowUpdate() {
    if (!meeting?.meeting_id) return;
    try {
      const raw = await api.getMeeting(meeting.meeting_id);
      meeting.actions = raw.action_items || [];
      meeting.minutes_markdown = raw.minutes?.markdown_content || meeting.minutes_markdown;
    } catch {
      // best-effort — the row already reflects the change locally
    }
  }

  const tabs = $derived([
    { key: 'minutes', label: 'Minutes' },
    // Post-hoc paste box for notes exported from a meeting app — sits
    // between Minutes and Transcript so it's easy to land on after reading
    // the generated summary.
    { key: 'external', label: 'External notes' },
    { key: 'transcript', label: 'Transcript' },
    { key: 'actions', label: actionsTabLabel(meeting) },
    { key: 'decisions', label: `Decisions${meeting?.decisions?.length ? ` (${meeting.decisions.length})` : ''}` },
    { key: 'analytics', label: 'Analytics' }
  ]);

  function actionsTabLabel(m) {
    if (!m?.actions?.length) return 'Actions';
    const proposed = m.actions.filter(a => (a.proposal_state || 'confirmed') === 'proposed').length;
    const confirmed = m.actions.filter(a => (a.proposal_state || 'confirmed') === 'confirmed').length;
    if (proposed > 0 && confirmed > 0) return `Actions (${confirmed} · ${proposed} to review)`;
    if (proposed > 0) return `Actions (${proposed} to review)`;
    return `Actions (${confirmed})`;
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    // Handle both "2026-03-30" and "2026-03-30T00:00:00" formats
    const raw = dateStr.length === 10 ? dateStr + 'T12:00:00' : dateStr;
    const d = new Date(raw);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  }

  function formatDuration(min) {
    if (!min) return '';
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    const m = min % 60;
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }

  function formatTimestamp(seconds) {
    if (seconds == null) return '';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  function isActiveSegment(segment) {
    if (!segment.start_time && segment.start_time !== 0) return false;
    const nextStart = segment.end_time || (segment.start_time + 30);
    return currentAudioTime >= segment.start_time && currentAudioTime < nextStart;
  }

  async function loadMeeting(id) {
    loading = true;
    meeting = null;
    transcript = null;
    activeTab = 'minutes';
    showAllAttendees = false;
    try {
      const raw = await api.getMeeting(id);
      meeting = {
        ...raw,
        type: raw.meeting_type,
        attendees: (raw.attendees || []).map(a => typeof a === 'string' ? a : (a.name || a.email || '')),
        actions: raw.action_items || [],
        decisions: raw.decisions || [],
        minutes_markdown: raw.minutes?.markdown_content || null,
        summary: raw.minutes?.summary || raw.summary || null,
        detailed_notes: raw.minutes?.detailed_notes || null,
        duration: raw.duration,
        participant_sentiments: raw.participant_sentiments || {},
        effectiveness_score: raw.effectiveness_score || 0,
        // Structured minutes fields
        discussion_points: raw.minutes?.discussion_points || [],
        risks_and_concerns: raw.minutes?.risks_and_concerns || [],
        follow_ups: raw.minutes?.follow_ups || [],
        parking_lot: raw.minutes?.parking_lot || [],
        key_topics: raw.minutes?.key_topics || [],
        sections: raw.minutes?.sections || [],
        sentiment: raw.minutes?.sentiment || null,
        external_notes: raw.external_notes || '',
        external_notes_status: raw.external_notes_status || null,
        external_notes_error: raw.external_notes_error || null,
        regen_status: raw.regen_status || null,
        regen_error: raw.regen_error || null,
      };
      // Keep the textarea in sync with whatever the server currently has.
      // On first load this pre-fills the paste; on status-poll refreshes
      // we'd otherwise clobber an in-progress edit, but that's OK — the
      // textarea is disabled while submittingExternalNotes is true.
      externalNotesDraft = raw.external_notes || '';
      // If we landed on a meeting that's already mid-processing (e.g. the
      // user navigated away and came back), resume polling so the pill
      // updates without requiring a new submit.
      if (raw.external_notes_status === 'processing' && !externalNotesPoller) {
        startExternalNotesPolling();
      }
      // Same logic for any in-flight regeneration (type change, speaker
      // rename, …): if the user navigated away mid-regeneration and came
      // back, resume polling. Generic toast strings — we don't know which
      // trigger started it.
      if (raw.regen_status === 'processing' && !regenPoller) {
        startRegenPolling({
          successToast: 'Minutes regenerated',
          errorPrefix: 'Regeneration failed',
        });
      }
      // Reset expanded discussion topics on new meeting load
      expandedTopics = new Set();
    } catch (e) {
      console.error('Failed to load meeting:', e);
      addToast('Failed to load meeting', 'error');
    } finally {
      loading = false;
    }
  }

  async function loadTranscript(id) {
    try {
      transcript = await api.getTranscript(id);
    } catch (e) {
      if (meeting?.transcript_text) {
        transcript = { full_text: meeting.transcript_text, segments: [] };
      }
    }
  }

  async function loadAnalytics(id) {
    try {
      analytics = await api.getAnalytics(id);
    } catch (e) {
      analytics = null;
    }
  }

  async function handleRegenerate() {
    // /regenerate is async — server returns 202 with regen_status=processing
    // and the background task flips it to ready/error. Same pattern as the
    // type-change and rename flows.
    regenerating = true;
    try {
      await api.regenerateMeeting(meetingId);
      addToast('Regenerating minutes…', 'info');
      // Refresh so the regen pill shows "processing", then poll until done.
      await loadMeeting(meetingId);
      startRegenPolling({
        successToast: 'Minutes regenerated',
        errorPrefix: 'Regeneration failed',
      });
    } catch (e) {
      addToast(`Failed to regenerate minutes: ${e.message}`, 'error');
    } finally {
      regenerating = false;
    }
  }

  async function handleDelete() {
    try {
      await api.deleteMeeting(meetingId);
      addToast('Meeting deleted', 'success');
      if (onDelete) {
        onDelete(meetingId);
      } else {
        goto('/');
      }
    } catch (e) {
      addToast('Failed to delete meeting', 'error');
    }
  }

  async function handleTagsChange(tags) {
    try {
      await api.updateMeeting(meetingId, { tags });
    } catch (e) {
      addToast('Failed to update tags', 'error');
    }
  }

  function openTitleEditor() {
    pendingTitle = meeting?.title || '';
    editingTitle = true;
  }

  function cancelTitleEdit() {
    editingTitle = false;
    pendingTitle = '';
  }

  async function saveTitle() {
    const next = (pendingTitle || '').trim();
    if (!next) {
      addToast('Title cannot be empty', 'warning');
      return;
    }
    if (next === (meeting?.title || '')) {
      editingTitle = false;
      return;
    }
    savingTitle = true;
    try {
      await api.updateMeeting(meetingId, { title: next });
      // Reload so the embedded markdown heading + any other derived fields
      // pick up the new title.
      await loadMeeting(meetingId);
      editingTitle = false;
      addToast('Title updated', 'success');
    } catch (e) {
      addToast(`Failed to update title: ${e.message}`, 'error');
    } finally {
      savingTitle = false;
    }
  }

  function handleTitleKeydown(event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      saveTitle();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelTitleEdit();
    }
  }

  function sentimentColor(sentiment) {
    switch (sentiment) {
      case 'positive': return 'bg-green-400';
      case 'negative': return 'bg-red-400';
      case 'mixed': return 'bg-yellow-400';
      default: return 'bg-gray-400';
    }
  }

  function sentimentLabel(sentiment) {
    return sentiment ? sentiment.charAt(0).toUpperCase() + sentiment.slice(1) : 'Unknown';
  }

  function seekToTimestamp(seconds) {
    audioPlayerRef?.seekTo(seconds);
  }

  // Watch for meetingId changes and reload
  $effect(() => {
    const id = meetingId;
    if (id) {
      // Stop any in-flight external-notes poller from the previous meeting
      // before we kick off loads for the new one — loadMeeting() will
      // re-start polling if the new meeting is still processing.
      stopExternalNotesPolling();
      stopRegenPolling();
      // Reset the inline editor when switching to a different meeting.
      editingType = false;
      pendingType = null;
      loadMeeting(id);
      loadTranscript(id);
      loadAnalytics(id);
      loadSpeakerSuggestions(id);
      loadMeetingSeries(id);
    }
    // Cleanup when this component unmounts.
    return () => {
      stopExternalNotesPolling();
      stopRegenPolling();
    };
  });
</script>

<div class="p-6">
  {#if loading}
    <div class="space-y-4">
      <Skeleton type="text" lines={2} />
      <Skeleton type="text" lines={6} />
    </div>
  {:else if meeting}
    <!-- Header -->
    {#if editingTitle}
      <div class="flex items-center gap-2 mb-3">
        <input
          type="text"
          bind:value={pendingTitle}
          onkeydown={handleTitleKeydown}
          disabled={savingTitle}
          maxlength="200"
          placeholder="Meeting title"
          autofocus
          class="flex-1 text-2xl font-bold text-[var(--text-primary)] bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
        />
        <button
          onclick={saveTitle}
          disabled={savingTitle || !pendingTitle.trim()}
          class="px-3 py-1.5 bg-[var(--accent)] text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-40"
        >
          {savingTitle ? 'Saving…' : 'Save'}
        </button>
        <button
          onclick={cancelTitleEdit}
          disabled={savingTitle}
          class="px-3 py-1.5 bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-secondary)] rounded-lg text-sm hover:text-[var(--text-primary)]"
        >
          Cancel
        </button>
      </div>
    {:else}
      <div class="flex items-center gap-2 mb-3">
        <h1 class="text-2xl font-bold text-[var(--text-primary)]">
          {meeting.title || 'Untitled Meeting'}
        </h1>
        <button
          onclick={openTitleEditor}
          class="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] underline-offset-2 hover:underline"
          title="Rename this meeting"
        >
          Edit
        </button>
      </div>
    {/if}

    <!-- Metadata pills -->
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <!--
        Meeting type: editable inline. Three render states:
          - default        → badge + small "Change" button.
          - editingType    → <select> grouped by category + Cancel + Regenerate.
          - status pill    → "Regenerating…" while ANY background regen runs
                             (type change, speaker rename, …). Polled via
                             meeting.regen_status.
      -->
      {#if meeting.regen_status === 'processing'}
        <span class="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-medium bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
          <svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
          </svg>
          Regenerating minutes…
        </span>
      {:else if editingType}
        <div class="inline-flex items-center gap-2 flex-wrap">
          <select
            bind:value={pendingType}
            disabled={submittingTypeChange}
            class="px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
          >
            {#each MEETING_TYPE_GROUPS as g}
              <optgroup label={g.group}>
                {#each g.items as t}
                  <option value={t.value}>{t.label}</option>
                {/each}
              </optgroup>
            {/each}
          </select>
          <button
            onclick={requestTypeChange}
            disabled={submittingTypeChange || !pendingType || pendingType === meeting.type}
            class="px-2.5 py-1 bg-[var(--accent)] text-white rounded-full text-xs font-medium hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Regenerate
          </button>
          <button
            onclick={cancelTypeEdit}
            disabled={submittingTypeChange}
            class="px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-secondary)] rounded-full text-xs hover:text-[var(--text-primary)]"
          >
            Cancel
          </button>
        </div>
      {:else}
        <div class="inline-flex items-center gap-1.5">
          <MeetingTypeBadge type={meeting.type} />
          {#if meeting.transcript_text || meeting.minutes_markdown}
            <button
              onclick={openTypeEditor}
              class="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] underline-offset-2 hover:underline"
              title="Change meeting type and regenerate the summary"
            >
              Change
            </button>
          {/if}
        </div>
      {/if}
      {#if meeting.regen_status === 'error'}
        <span
          class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs bg-red-500/15 text-red-400 border border-red-500/30"
          title={meeting.regen_error || 'Regeneration failed'}
        >
          Regeneration failed
        </span>
      {/if}
      {#if meeting.duration_minutes}
        <span class="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-secondary)]">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          {formatDuration(meeting.duration_minutes)}
        </span>
      {/if}
      {#if meeting.date}
        <span class="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-secondary)]">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
          {formatDate(meeting.date)}
        </span>
      {/if}
      {#if meeting.attendees?.length}
        <span class="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-secondary)]">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
          {meeting.attendees.length} people
        </span>
      {/if}
      {#if meeting.effectiveness_score > 0}
        <span class="inline-flex items-center gap-0.5 px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full text-xs" title="Effectiveness score: {meeting.effectiveness_score}/5">
          {#each Array(5) as _, i}
            <span class="{i < meeting.effectiveness_score ? 'text-yellow-400' : 'text-[var(--text-muted)]'}">★</span>
          {/each}
        </span>
      {/if}
      {#if meetingSeries}
        <a
          href="/series/{meetingSeries.series_id}"
          class="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--accent)]/10 border border-[var(--accent)]/30 rounded-full text-xs text-[var(--accent)] hover:opacity-90"
          title="Part of series: {meetingSeries.title}"
        >
          Part of series: {meetingSeries.title} →
        </a>
      {/if}
    </div>

    <!-- Attendees -->
    {#if meeting.attendees?.length}
      <div class="mb-6">
        <div class="flex items-center gap-2 flex-wrap">
          {#each (showAllAttendees ? meeting.attendees : meeting.attendees.slice(0, 5)) as attendee}
            <div class="flex items-center gap-1.5 px-2 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full">
              <PersonAvatar name={attendee} size="sm" />
              <span class="text-xs text-[var(--text-secondary)]">{attendee}</span>
              {#if meeting.participant_sentiments?.[attendee]}
                <span
                  class="w-2 h-2 rounded-full {sentimentColor(meeting.participant_sentiments[attendee])}"
                  title="{sentimentLabel(meeting.participant_sentiments[attendee])}"
                ></span>
              {/if}
            </div>
          {/each}
          {#if !showAllAttendees && meeting.attendees.length > 5}
            <button
              onclick={() => showAllAttendees = true}
              class="text-xs text-[var(--accent)] hover:underline"
            >
              +{meeting.attendees.length - 5} more
            </button>
          {/if}
        </div>
      </div>
    {/if}

    <!-- Tabs -->
    <div class="border-b border-[var(--border-subtle)] mb-6">
      <div class="flex gap-0">
        {#each tabs as tab}
          <button
            onclick={() => activeTab = tab.key}
            class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors duration-150
                   {activeTab === tab.key
                     ? 'text-[var(--accent)] border-[var(--accent)]'
                     : 'text-[var(--text-secondary)] border-transparent hover:text-[var(--text-primary)] hover:border-[var(--border-subtle)]'}"
          >
            {tab.label}
          </button>
        {/each}
      </div>
    </div>

    <!-- Tab content -->
    <div class="mb-8">
      {#if activeTab === 'minutes'}
        {#if meeting.minutes_markdown || meeting.summary || meeting.detailed_notes || meeting.discussion_points?.length || meeting.sections?.length}
          <!-- Toggle: structured view vs raw markdown -->
          <div class="flex items-center justify-end gap-2 mb-4">
            {#if meeting.discussion_points?.length}
              <button
                onclick={() => expandedTopics.size === meeting.discussion_points.length ? collapseAllTopics() : expandAllTopics()}
                class="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] px-2 py-1"
              >
                {expandedTopics.size === meeting.discussion_points.length ? 'Collapse all' : 'Expand all'}
              </button>
              <span class="text-[var(--border-subtle)]">·</span>
            {/if}
            <button
              onclick={() => showRawMarkdown = !showRawMarkdown}
              class="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] px-2 py-1"
            >
              {showRawMarkdown ? 'Structured view' : 'Raw markdown'}
            </button>
          </div>

          {#if showRawMarkdown}
            <!-- Raw markdown fallback for users who prefer it -->
            <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
              <MarkdownRenderer content={meeting.minutes_markdown} />
            </div>
          {:else}
            <div class="space-y-5">
              <!-- Summary card with accent border -->
              {#if meeting.summary}
                <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden">
                  <div class="flex">
                    <div class="w-1 bg-[var(--accent)]"></div>
                    <div class="flex-1 p-5">
                      <div class="flex items-center justify-between mb-2">
                        <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">Summary</h3>
                        {#if meeting.sentiment}
                          <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium {sentimentBadgeClass(meeting.sentiment)}">
                            <span class="w-1.5 h-1.5 rounded-full bg-current"></span>
                            {sentimentLabel(meeting.sentiment)}
                          </span>
                        {/if}
                      </div>
                      <p class="text-sm text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">{meeting.summary}</p>
                    </div>
                  </div>
                </div>
              {/if}

              <!-- Detailed narrative notes -->
              {#if meeting.detailed_notes}
                <details class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden group" open>
                  <summary class="cursor-pointer px-5 py-3 flex items-center justify-between hover:bg-[var(--bg-hover)]">
                    <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">Detailed Notes</h3>
                    <svg class="w-4 h-4 text-[var(--text-muted)] transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                    </svg>
                  </summary>
                  <div class="px-5 pb-5 pt-1 border-t border-[var(--border-subtle)]">
                    <MarkdownRenderer content={meeting.detailed_notes} />
                  </div>
                </details>
              {/if}

              <!-- Key topics chips -->
              {#if meeting.key_topics?.length}
                <div class="flex flex-wrap gap-1.5">
                  {#each meeting.key_topics as topic}
                    <span class="inline-flex items-center px-2.5 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-secondary)]">
                      #{topic}
                    </span>
                  {/each}
                </div>
              {/if}

              <!-- Discussion topics (collapsible cards) -->
              {#if meeting.discussion_points?.length}
                <div>
                  <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">
                    Discussion ({meeting.discussion_points.length})
                  </h3>
                  <div class="space-y-2">
                    {#each meeting.discussion_points as topic, idx}
                      {@const isOpen = expandedTopics.has(idx)}
                      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden transition-colors hover:border-[var(--text-muted)]">
                        <button
                          onclick={() => toggleTopic(idx)}
                          class="w-full flex items-start gap-3 p-4 text-left"
                        >
                          <svg
                            class="w-4 h-4 mt-0.5 text-[var(--text-muted)] shrink-0 transition-transform {isOpen ? 'rotate-90' : ''}"
                            fill="none" stroke="currentColor" viewBox="0 0 24 24"
                          >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                          </svg>
                          <div class="flex-1 min-w-0">
                            <div class="flex items-center justify-between gap-3 flex-wrap">
                              <h4 class="text-sm font-semibold text-[var(--text-primary)]">{topic.topic || 'Untitled topic'}</h4>
                              <div class="flex items-center gap-2">
                                {#if topic.sentiment}
                                  <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border text-[10px] font-medium {sentimentBadgeClass(topic.sentiment)}">
                                    {sentimentLabel(topic.sentiment)}
                                  </span>
                                {/if}
                                {#if topic.participants?.length}
                                  <div class="flex -space-x-1">
                                    {#each topic.participants.slice(0, 3) as p}
                                      <div title={p} class="ring-2 ring-[var(--bg-surface)] rounded-full">
                                        <PersonAvatar name={p} size="sm" />
                                      </div>
                                    {/each}
                                    {#if topic.participants.length > 3}
                                      <span class="ring-2 ring-[var(--bg-surface)] w-6 h-6 rounded-full bg-[var(--bg-surface-hover)] text-[10px] text-[var(--text-muted)] flex items-center justify-center">
                                        +{topic.participants.length - 3}
                                      </span>
                                    {/if}
                                  </div>
                                {/if}
                              </div>
                            </div>
                            {#if !isOpen && topic.summary}
                              <p class="text-xs text-[var(--text-muted)] mt-1 line-clamp-2">{topic.summary}</p>
                            {/if}
                          </div>
                        </button>
                        {#if isOpen}
                          <div class="px-4 pb-4 pl-11">
                            {#if topic.summary}
                              <p class="text-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">{topic.summary}</p>
                            {/if}
                            {#if topic.participants?.length}
                              <div class="mt-3 flex flex-wrap gap-1.5">
                                {#each topic.participants as p}
                                  <span class="inline-flex items-center gap-1 px-2 py-0.5 bg-[var(--bg-surface-hover)] rounded-full text-[11px] text-[var(--text-secondary)]">
                                    <PersonAvatar name={p} size="sm" />
                                    {p}
                                  </span>
                                {/each}
                              </div>
                            {/if}
                          </div>
                        {/if}
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}

              <!-- Sections fallback: used by older meetings and text+regex path
                   where the LLM produced markdown sections instead of structured
                   discussion_points. Only renders when there are no discussion
                   points AND there are sections to show. -->
              {#if !meeting.discussion_points?.length && meeting.sections?.length}
                {@const renderableSections = meeting.sections.filter(s => {
                  const h = (s.heading || '').toLowerCase();
                  const t = (s.type || '').toLowerCase();
                  // Skip sections that are already rendered as dedicated cards
                  return !['summary', 'action items', 'actions', 'decisions', 'key topics',
                          'risks & concerns', 'risks and concerns', 'follow-ups', 'follow ups',
                          'parking lot'].includes(h) &&
                         !['summary', 'action_items', 'decisions', 'key_topics'].includes(t);
                })}
                {#if renderableSections.length}
                  <div>
                    <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">
                      Discussion ({renderableSections.length})
                    </h3>
                    <div class="space-y-2">
                      {#each renderableSections as section, idx}
                        {@const isOpen = expandedTopics.has(1000 + idx)}
                        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden transition-colors hover:border-[var(--text-muted)]">
                          <button
                            onclick={() => toggleTopic(1000 + idx)}
                            class="w-full flex items-start gap-3 p-4 text-left"
                          >
                            <svg
                              class="w-4 h-4 mt-0.5 text-[var(--text-muted)] shrink-0 transition-transform {isOpen ? 'rotate-90' : ''}"
                              fill="none" stroke="currentColor" viewBox="0 0 24 24"
                            >
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                            </svg>
                            <div class="flex-1 min-w-0">
                              <h4 class="text-sm font-semibold text-[var(--text-primary)]">{section.heading || 'Section'}</h4>
                              {#if !isOpen && section.content}
                                <p class="text-xs text-[var(--text-muted)] mt-1 line-clamp-2">{section.content.slice(0, 200)}</p>
                              {/if}
                            </div>
                          </button>
                          {#if isOpen && section.content}
                            <div class="px-4 pb-4 pl-11">
                              <p class="text-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">{section.content}</p>
                            </div>
                          {/if}
                        </div>
                      {/each}
                    </div>
                  </div>
                {/if}
              {/if}

              <!-- Outcomes grid: Decisions + Actions (compact preview, full lists in tabs) -->
              {#if meeting.decisions?.length || meeting.actions?.length}
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {#if meeting.decisions?.length}
                    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
                      <div class="flex items-center justify-between mb-3">
                        <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                          Decisions ({meeting.decisions.length})
                        </h3>
                        <button
                          onclick={() => activeTab = 'decisions'}
                          class="text-[11px] text-[var(--accent)] hover:underline"
                        >
                          View all →
                        </button>
                      </div>
                      <ul class="space-y-2">
                        {#each meeting.decisions.slice(0, 3) as d}
                          <li class="flex gap-2 text-sm text-[var(--text-primary)]">
                            <span class="text-[var(--accent)] mt-0.5">◆</span>
                            <span class="flex-1">{d.description}</span>
                          </li>
                        {/each}
                      </ul>
                    </div>
                  {/if}
                  {#if meeting.actions?.length}
                    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
                      <div class="flex items-center justify-between mb-3">
                        <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                          Action items
                          <span class="ml-1 normal-case font-normal text-[var(--text-muted)]">
                            {confirmedActions.length} confirmed{#if proposedActions.length} · <span class="text-[var(--accent)] font-medium">{proposedActions.length} to review</span>{/if}
                          </span>
                        </h3>
                        <button
                          onclick={() => activeTab = 'actions'}
                          class="text-[11px] text-[var(--accent)] hover:underline"
                        >
                          {proposedActions.length ? 'Review →' : 'View all →'}
                        </button>
                      </div>
                      <ul class="space-y-2">
                        {#each meeting.actions.slice(0, 3) as a}
                          {@const ps = a.proposal_state || 'confirmed'}
                          <li class="flex gap-2 text-sm">
                            <span class="mt-0.5 {ps === 'proposed' ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]'}">
                              {ps === 'proposed' ? '?' : '○'}
                            </span>
                            <div class="flex-1 min-w-0">
                              <span class="text-[var(--text-primary)] {ps === 'rejected' ? 'line-through opacity-60' : ''}">{a.description}</span>
                              {#if a.owner}
                                <span class="text-xs text-[var(--text-muted)] ml-1">— {a.owner}</span>
                              {/if}
                            </div>
                          </li>
                        {/each}
                      </ul>
                    </div>
                  {/if}
                </div>
              {/if}

              <!-- Risks & Concerns -->
              {#if meeting.risks_and_concerns?.length}
                <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg overflow-hidden">
                  <div class="flex">
                    <div class="w-1 bg-yellow-500"></div>
                    <div class="flex-1 p-5">
                      <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">
                        Risks & Concerns ({meeting.risks_and_concerns.length})
                      </h3>
                      <ul class="space-y-2">
                        {#each meeting.risks_and_concerns as risk}
                          <li class="flex gap-2 text-sm text-[var(--text-primary)]">
                            <span class="text-yellow-500 mt-0.5">⚠</span>
                            <div class="flex-1">
                              <span>{risk.description}</span>
                              {#if risk.raised_by}
                                <span class="text-xs text-[var(--text-muted)] ml-1">— {risk.raised_by}</span>
                              {/if}
                            </div>
                          </li>
                        {/each}
                      </ul>
                    </div>
                  </div>
                </div>
              {/if}

              <!-- Follow-ups -->
              {#if meeting.follow_ups?.length}
                <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
                  <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">
                    Follow-ups ({meeting.follow_ups.length})
                  </h3>
                  <ul class="space-y-2">
                    {#each meeting.follow_ups as f}
                      <li class="flex gap-2 text-sm text-[var(--text-primary)]">
                        <span class="text-[var(--text-muted)] mt-0.5">→</span>
                        <div class="flex-1">
                          <span>{f.description}</span>
                          {#if f.owner}
                            <span class="text-xs text-[var(--text-muted)] ml-1">— {f.owner}</span>
                          {/if}
                          {#if f.timeframe}
                            <span class="inline-flex items-center px-1.5 py-0.5 ml-2 bg-[var(--bg-surface-hover)] rounded text-[10px] text-[var(--text-muted)]">{f.timeframe}</span>
                          {/if}
                        </div>
                      </li>
                    {/each}
                  </ul>
                </div>
              {/if}

              <!-- Parking lot -->
              {#if meeting.parking_lot?.length}
                <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] border-dashed rounded-lg p-5">
                  <h3 class="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] mb-3">
                    Parking lot ({meeting.parking_lot.length})
                  </h3>
                  <ul class="space-y-1.5">
                    {#each meeting.parking_lot as item}
                      <li class="text-sm text-[var(--text-secondary)]">• {item}</li>
                    {/each}
                  </ul>
                </div>
              {/if}

              <!-- Fallback: if no structured data, show markdown -->
              {#if !meeting.summary && !meeting.discussion_points?.length && !meeting.sections?.length && meeting.minutes_markdown}
                <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
                  <MarkdownRenderer content={meeting.minutes_markdown} />
                </div>
              {/if}
            </div>
          {/if}
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No minutes generated yet.</p>
        {/if}

      {:else if activeTab === 'external'}
        <!--
          External notes tab. Post-hoc only: the user pastes notes from a
          meeting app (Teams/Zoom/Meet/Gemini/Otter/…) and on submit the
          server:
            1. archives the paste at data/external_notes/{id}.md,
            2. kicks off a background job that re-runs speaker naming and
               minutes generation using the paste as extra context,
            3. appends a verbatim "## External notes" section to the
               meeting's markdown (and the Obsidian file).
          We poll /meetings/{id} until the status flips from "processing"
          to "ready" (or "error").
        -->
        <div class="space-y-4 max-w-3xl">
          <div>
            <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-1">
              External notes
            </h3>
            <p class="text-xs text-[var(--text-muted)] leading-relaxed">
              Paste notes from Teams, Zoom, Google Meet / Gemini, Otter, etc.
              The raw paste is saved under a <code class="text-[11px] bg-[var(--bg-surface)] px-1 rounded">## External notes</code>
              section in your meeting's markdown (and Obsidian), and we use
              the content to re-run speaker naming and re-generate the
              summary in the background.
            </p>
          </div>

          {#if meeting.external_notes_status === 'processing'}
            <div class="flex items-center gap-2 px-3 py-2 rounded-md bg-blue-500/10 border border-blue-500/30 text-xs text-blue-300">
              <svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              Regenerating minutes in the background — this usually takes 15–60 seconds.
            </div>
          {:else if meeting.external_notes_status === 'error'}
            <div class="px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-xs text-red-300">
              <div class="font-medium">Last update failed</div>
              <div class="mt-0.5 text-[var(--text-muted)]">{meeting.external_notes_error || 'Unknown error'}</div>
            </div>
          {:else if meeting.external_notes_status === 'ready' && meeting.external_notes}
            <div class="px-3 py-2 rounded-md bg-green-500/10 border border-green-500/30 text-xs text-green-300">
              Minutes were updated using these notes. Edit and re-submit to run again.
            </div>
          {/if}

          <textarea
            bind:value={externalNotesDraft}
            rows="16"
            placeholder="Paste the meeting-app notes here..."
            disabled={submittingExternalNotes || meeting.external_notes_status === 'processing'}
            class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-md text-sm text-[var(--text-primary)] font-mono leading-relaxed focus:outline-none focus:border-[var(--accent)] disabled:opacity-60"
          ></textarea>

          <div class="flex items-center justify-between gap-3">
            <div class="text-xs text-[var(--text-muted)]">
              {externalNotesDraft.length.toLocaleString()} characters
            </div>
            <button
              onclick={submitExternalNotes}
              disabled={submittingExternalNotes || meeting.external_notes_status === 'processing' || !externalNotesDraft.trim()}
              class="px-4 py-2 text-sm font-medium rounded-md bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submittingExternalNotes ? 'Saving…' : 'Save & update minutes'}
            </button>
          </div>
        </div>

      {:else if activeTab === 'transcript'}
        {#if meeting.audio_path}
          <div class="mb-4">
            <AudioPlayer
              bind:this={audioPlayerRef}
              src="/api/meetings/{meetingId}/audio"
              onTimeUpdate={(t) => currentAudioTime = t}
            />
          </div>
        {/if}

        {#if transcript?.segments?.length}
          {@const speakerMap = Object.fromEntries((transcript.speakers || []).map(s => [s.label, s.name || s.label]))}
          {@const speakerColors = ['#6366F1', '#0EA5E9', '#22C55E', '#F59E0B', '#EC4899', '#F97316', '#14B8A6', '#A855F7']}
          {@const uniqueSpeakers = [...new Set(transcript.segments.map(s => s.speaker).filter(Boolean))]}
          {@const colorFor = (label) => label ? speakerColors[uniqueSpeakers.indexOf(label) % speakerColors.length] : '#6B7280'}
          {@const hasGenericLabels = uniqueSpeakers.some(s => /^SPEAKER_\d+$/.test(s))}

          <!-- Speaker legend + edit button -->
          {#if uniqueSpeakers.length > 0}
            <div class="flex items-center justify-between flex-wrap gap-2 mb-3 pb-3 border-b border-[var(--border-subtle)]">
              <div class="flex flex-wrap gap-2">
                {#each uniqueSpeakers as label}
                  <span class="inline-flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
                    <span class="w-2 h-2 rounded-full" style="background-color: {colorFor(label)}"></span>
                    {speakerMap[label] || label}
                  </span>
                {/each}
              </div>
              <button
                onclick={openSpeakerEditor}
                class="text-xs text-[var(--accent)] hover:underline"
                title="Rename speakers and regenerate minutes"
              >
                {hasGenericLabels ? '✎ Name speakers' : '✎ Edit names'}
              </button>
            </div>
          {/if}

          <!-- Speaker rename editor -->
          {#if showSpeakerEditor}
            <!--
              ``editorSpeakers`` extends the legend's ``uniqueSpeakers`` with
              a synthetic Unknown row when any segment has a null/empty
              speaker (e.g. diarization gaps). The Unknown row isn't shown
              in the legend above, but it must be renameable.
            -->
            {@const hasUnassigned = transcript.segments.some(s => !s.speaker)}
            {@const editorSpeakers = hasUnassigned ? [...uniqueSpeakers, UNKNOWN_CLUSTER] : uniqueSpeakers}
            <div class="mb-4 p-4 bg-[var(--bg-surface)] border border-[var(--accent)] rounded-lg">
              <!--
                Single shared <datalist> populated from /people. Each rename
                input references it via list="known-people" — the browser
                handles the autocomplete dropdown for free.
              -->
              <datalist id="known-people">
                {#each knownPeople as p}
                  <option value={p.name}></option>
                {/each}
              </datalist>
              <h4 class="text-sm font-semibold text-[var(--text-primary)] mb-1">Rename speakers</h4>
              <p class="text-xs text-[var(--text-muted)] mb-3">
                Voice samples are learned automatically — suggested names come from previous meetings. Type a known name to link voices across meetings, or a new name to add a person on the fly. Minutes regenerate in the background.
              </p>
              <div class="space-y-2 mb-3">
                {#each editorSpeakers as label}
                  {@const isUnknown = label === UNKNOWN_CLUSTER}
                  {@const sugg = isUnknown ? {} : (speakerSuggestions[label] || {})}
                  {@const tier = sugg.suggestion_tier}
                  {@const speechSec = sugg.speech_seconds || 0}
                  {@const canCreateNew = !isUnknown && speechSec > 30 && !tier}
                  {@const matchedPerson = resolveTypedName(speakerEdits[label])}
                  {@const displayLabel = isUnknown ? 'Unknown' : label}
                  {@const swatchColor = isUnknown ? '#6B7280' : colorFor(label)}
                  <div class="space-y-1.5">
                    <div class="flex items-center gap-3">
                      <span class="w-2 h-2 rounded-full shrink-0" style="background-color: {swatchColor}"></span>
                      <span class="text-xs font-mono text-[var(--text-muted)] w-24 shrink-0">{displayLabel}</span>
                      <input
                        type="text"
                        list="known-people"
                        bind:value={speakerEdits[label]}
                        oninput={() => {
                          // Keep speakerPersonIds in sync with what was typed
                          // so person_mapping is sent for matched names. The
                          // server resolves anyway, but this avoids an extra
                          // round-trip for the common case.
                          const m = resolveTypedName(speakerEdits[label]);
                          speakerPersonIds = { ...speakerPersonIds, [label]: m ? m.person_id : '' };
                        }}
                        placeholder="e.g. Tom"
                        class="flex-1 px-2 py-1 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded text-sm text-[var(--text-primary)]
                               focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                      />
                      {#if tier === 'high' && sugg.suggested_name}
                        <span
                          class="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-green-500/15 text-green-400 border border-green-500/30 shrink-0"
                          title="Matched {sugg.suggested_name} with {(sugg.suggestion_score * 100).toFixed(0)}% similarity"
                        >
                          suggested
                        </span>
                      {:else if tier === 'medium' && sugg.suggested_name}
                        <span
                          class="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 shrink-0"
                          title="Best guess: {sugg.suggested_name} ({(sugg.suggestion_score * 100).toFixed(0)}% similarity) — confirm or replace"
                        >
                          ? {sugg.suggested_name}
                        </span>
                      {/if}
                    </div>
                    <!--
                      Below-input ambient feedback. Three states:
                        - Typed name matches a known person  → "Will link to <name>"
                        - Typed name is new (and they typed one) → "New person — will be created"
                        - Empty → nothing
                    -->
                    {#if speakerEdits[label]?.trim() && matchedPerson}
                      <div class="ml-[9px] pl-6 text-[11px] text-[var(--text-muted)]">
                        Will link to existing person <span class="text-[var(--text-secondary)]">{matchedPerson.name}</span>
                        {#if matchedPerson.email}<span class="text-[var(--text-muted)]"> · {matchedPerson.email}</span>{/if}
                      </div>
                    {:else if speakerEdits[label]?.trim() && !speakerEdits[label].startsWith('SPEAKER_')}
                      <div class="ml-[9px] pl-6 text-[11px] text-[var(--text-muted)]">
                        New name — a person will be created and the voice signature stored.
                      </div>
                    {/if}
                    {#if canCreateNew && !newPersonForms[label]?.open}
                      <div class="ml-[9px] pl-6 text-[11px] text-[var(--text-muted)]">
                        {Math.round(speechSec)}s of speech, no match.
                        <button
                          type="button"
                          class="text-[var(--accent)] hover:underline"
                          onclick={() => openNewPersonForm(label)}
                        >
                          + Create new person
                        </button>
                      </div>
                    {/if}
                    {#if newPersonForms[label]?.open}
                      <div class="ml-[9px] pl-6 flex items-center gap-2">
                        <input
                          type="text"
                          placeholder="Name"
                          bind:value={newPersonForms[label].name}
                          class="flex-1 px-2 py-1 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded text-xs text-[var(--text-primary)]
                                 focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        />
                        <input
                          type="email"
                          placeholder="Email (optional)"
                          bind:value={newPersonForms[label].email}
                          class="flex-1 px-2 py-1 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded text-xs text-[var(--text-primary)]
                                 focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        />
                        <button
                          type="button"
                          disabled={newPersonForms[label].saving}
                          onclick={() => submitNewPerson(label)}
                          class="px-2 py-1 bg-[var(--accent)] text-white text-xs rounded hover:opacity-90 disabled:opacity-50"
                        >
                          {newPersonForms[label].saving ? 'Saving…' : 'Add'}
                        </button>
                        <button
                          type="button"
                          onclick={() => closeNewPersonForm(label)}
                          class="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                        >
                          Cancel
                        </button>
                      </div>
                    {/if}
                  </div>
                {/each}
              </div>
              <div class="flex items-center gap-2">
                <button
                  onclick={() => saveSpeakerEdits(true)}
                  disabled={savingSpeakers}
                  class="px-3 py-1.5 bg-[var(--accent)] text-white text-xs font-medium rounded
                         hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  {savingSpeakers ? 'Saving...' : 'Save & regenerate minutes'}
                </button>
                <button
                  onclick={() => saveSpeakerEdits(false)}
                  disabled={savingSpeakers}
                  class="px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)]
                         border border-[var(--border-subtle)] rounded
                         hover:bg-[var(--bg-surface-hover)] disabled:opacity-50"
                >
                  Save only
                </button>
                <button
                  onclick={() => showSpeakerEditor = false}
                  disabled={savingSpeakers}
                  class="px-3 py-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                >
                  Cancel
                </button>
              </div>
            </div>
          {/if}

          <div class="space-y-1">
            {#each transcript.segments as segment, i}
              {@const displayName = speakerMap[segment.speaker] || segment.speaker || 'Unknown'}
              {@const speakerColor = colorFor(segment.speaker)}
              <div
                class="flex gap-3 p-3 rounded-lg transition-colors duration-150
                       {isActiveSegment(segment) ? 'bg-[var(--accent)] bg-opacity-5' : 'hover:bg-[var(--bg-surface-hover)]'}"
              >
                <button
                  onclick={() => seekToTimestamp(segment.start_time)}
                  class="text-xs font-mono text-[var(--text-muted)] hover:text-[var(--accent)] shrink-0 w-10 text-right"
                >
                  {formatTimestamp(segment.start_time)}
                </button>
                <div class="flex-1 min-w-0">
                  <span class="inline-flex items-center gap-1.5 text-sm font-semibold text-[var(--text-primary)]">
                    <span class="w-2 h-2 rounded-full shrink-0" style="background-color: {speakerColor}"></span>
                    {displayName}
                  </span>
                  <p class="text-sm text-[var(--text-secondary)] mt-0.5">{segment.text}</p>
                </div>
              </div>
            {/each}
          </div>
        {:else if transcript?.full_text}
          <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
            <p class="text-sm text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed">{transcript.full_text}</p>
          </div>
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No transcript available.</p>
        {/if}

      {:else if activeTab === 'actions'}
        {#if meeting.actions?.length}
          {#if proposedActions.length}
            <!-- Review banner: extracted action items haven't been blessed yet.
                 They stay out of the global tracker, exports, and prior-action
                 carryover until the user accepts (or rejects) them here. -->
            <div class="mb-4 p-4 rounded-lg border border-[var(--accent)]/40 bg-[var(--accent)]/5 flex items-center gap-3 flex-wrap">
              <div class="flex-1 min-w-[200px]">
                <p class="text-sm font-medium text-[var(--text-primary)]">
                  {proposedActions.length} proposed action{proposedActions.length === 1 ? '' : 's'} to review
                </p>
                <p class="text-xs text-[var(--text-muted)] mt-0.5">
                  Accept turns each into a tracked action. Edit before accepting if the wording is off.
                </p>
              </div>
              <div class="flex items-center gap-2">
                <button
                  onclick={acceptAllProposed}
                  disabled={bulkReviewing}
                  class="text-xs px-3 py-1.5 rounded bg-[var(--accent)] text-white hover:opacity-90 disabled:opacity-40"
                >
                  Accept all
                </button>
                <button
                  onclick={rejectAllProposed}
                  disabled={bulkReviewing}
                  class="text-xs px-3 py-1.5 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)] disabled:opacity-40"
                >
                  Reject all
                </button>
              </div>
            </div>
          {/if}
          <div class="space-y-1">
            {#each meeting.actions as item (item.action_item_id || item.id)}
              <ActionItemRow {item} showMeeting={false} onUpdate={onActionRowUpdate} />
            {/each}
          </div>
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No action items from this meeting.</p>
        {/if}

      {:else if activeTab === 'decisions'}
        {#if meeting.decisions?.length}
          <div class="space-y-3">
            {#each meeting.decisions as decision}
              <DecisionCard {decision} />
            {/each}
          </div>
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No decisions from this meeting.</p>
        {/if}

      {:else if activeTab === 'analytics'}
        {#if analytics}
          <!-- Talk-Time Distribution -->
          <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 mb-4">
            <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Talk-Time Distribution</h3>
            {#if !analytics.has_diarization}
              <p class="text-xs text-[var(--text-muted)] mb-3">Speaker diarization was not available for this meeting. All talk time is attributed to a single speaker.</p>
            {/if}
            <div class="space-y-3">
              {#each analytics.speakers as speaker}
                {@const barColors = ['#6366F1', '#0EA5E9', '#22C55E', '#F59E0B', '#EC4899', '#F97316', '#14B8A6', '#A855F7']}
                {@const colorIndex = analytics.speakers.indexOf(speaker) % barColors.length}
                <div>
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-sm font-medium text-[var(--text-primary)]">{speaker.speaker}</span>
                    <span class="text-xs text-[var(--text-muted)]">
                      {speaker.talk_time_percentage}% &middot; {Math.round(speaker.talk_time_seconds)}s &middot; {speaker.segment_count} segments
                    </span>
                  </div>
                  <div class="w-full h-3 bg-[var(--bg-surface-hover)] rounded-full overflow-hidden">
                    <div
                      class="h-full rounded-full transition-all duration-500"
                      style="width: {speaker.talk_time_percentage}%; background-color: {barColors[colorIndex]}"
                    ></div>
                  </div>
                </div>
              {/each}
            </div>
          </div>

          <!-- Question Frequency -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
              <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-3">Questions Asked</h3>
              <div class="space-y-2">
                {#each analytics.speakers.filter(s => s.question_count > 0) as speaker}
                  <div class="flex items-center justify-between">
                    <span class="text-sm text-[var(--text-secondary)]">{speaker.speaker}</span>
                    <span class="inline-flex items-center px-2 py-0.5 bg-[var(--bg-surface-hover)] rounded-full text-xs font-medium text-[var(--text-primary)]">
                      {speaker.question_count} question{speaker.question_count !== 1 ? 's' : ''}
                    </span>
                  </div>
                {/each}
                {#if analytics.speakers.every(s => s.question_count === 0)}
                  <p class="text-sm text-[var(--text-muted)] italic">No questions detected.</p>
                {/if}
              </div>
            </div>

            <!-- Monologues -->
            <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
              <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-3">Monologues (&gt;3 min)</h3>
              <div class="space-y-2">
                {#each analytics.speakers.filter(s => s.monologues.length > 0) as speaker}
                  {#each speaker.monologues as mono}
                    <div class="flex items-center justify-between">
                      <span class="text-sm text-[var(--text-secondary)]">{speaker.speaker}</span>
                      <span class="text-xs text-[var(--text-muted)]">
                        {Math.round(mono.duration_seconds / 60)}m {Math.round(mono.duration_seconds % 60)}s
                        at {Math.floor(mono.start / 60)}:{Math.floor(mono.start % 60).toString().padStart(2, '0')}
                      </span>
                    </div>
                  {/each}
                {/each}
                {#if analytics.speakers.every(s => s.monologues.length === 0)}
                  <p class="text-sm text-[var(--text-muted)] italic">No monologues detected.</p>
                {/if}
              </div>
            </div>
          </div>

          <!-- Summary stats -->
          <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
            <div class="grid grid-cols-3 gap-4 text-center">
              <div>
                <div class="text-2xl font-bold text-[var(--text-primary)]">{Math.round(analytics.total_duration_seconds / 60)}</div>
                <div class="text-xs text-[var(--text-muted)]">Minutes</div>
              </div>
              <div>
                <div class="text-2xl font-bold text-[var(--text-primary)]">{analytics.speakers.length}</div>
                <div class="text-xs text-[var(--text-muted)]">Speakers</div>
              </div>
              <div>
                <div class="text-2xl font-bold text-[var(--text-primary)]">{analytics.speakers.reduce((sum, s) => sum + s.question_count, 0)}</div>
                <div class="text-xs text-[var(--text-muted)]">Questions</div>
              </div>
            </div>
          </div>
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No analytics available for this meeting.</p>
        {/if}
      {/if}
    </div>

    <!-- Tags -->
    <div class="mb-8">
      <h3 class="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider mb-2">Tags</h3>
      <TagEditor
        tags={meeting.tags || []}
        onAdd={(tag) => handleTagsChange([...(meeting.tags || []), tag])}
        onRemove={(tag) => handleTagsChange((meeting.tags || []).filter(t => t !== tag))}
      />
    </div>

    <!-- Action bar -->
    <div class="flex items-center gap-3 pt-6 border-t border-[var(--border-subtle)]">
      <button
        onclick={handleRegenerate}
        disabled={regenerating}
        class="px-4 py-2 text-sm font-medium text-[var(--text-secondary)]
               border border-[var(--border-subtle)] rounded-lg
               hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]
               disabled:opacity-50 transition-colors duration-150"
      >
        {regenerating ? 'Regenerating...' : 'Regenerate'}
      </button>

      <ExportMenu
        meetingId={meetingId}
        meetingTitle={meeting?.title || meetingId}
      />

      <div class="ml-auto">
        <button
          onclick={() => showDeleteModal = true}
          class="px-4 py-2 text-sm font-medium text-[var(--danger)]
                 border border-[var(--danger)] border-opacity-30 rounded-lg
                 hover:bg-[var(--danger)] hover:bg-opacity-10
                 transition-colors duration-150"
        >
          Delete
        </button>
      </div>
    </div>
  {:else}
    <div class="flex items-center justify-center h-48">
      <p class="text-sm text-[var(--text-muted)]">Meeting not found</p>
    </div>
  {/if}
</div>

<ConfirmModal
  bind:open={showDeleteModal}
  title="Delete Meeting"
  message="This will permanently delete this meeting and all associated data. This action cannot be undone."
  confirmLabel="Delete"
  danger={true}
  onConfirm={handleDelete}
/>

<ConfirmModal
  bind:open={showTypeChangeModal}
  title="Regenerate as {pendingType ? (MEETING_TYPE_MAP[pendingType]?.label || pendingType) : ''}?"
  message={
    'This will discard the current minutes and rebuild them against the new template. The transcript, audio, and any pasted external notes are preserved. The new summary will be ready in 15-60 seconds.'
  }
  confirmLabel="Regenerate"
  onConfirm={confirmTypeChange}
  onCancel={() => { showTypeChangeModal = false; }}
/>
