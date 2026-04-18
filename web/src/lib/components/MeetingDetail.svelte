<script>
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  import MeetingTypeBadge from './MeetingTypeBadge.svelte';
  import MarkdownRenderer from './MarkdownRenderer.svelte';
  import AudioPlayer from './AudioPlayer.svelte';
  import ActionItemRow from './ActionItemRow.svelte';
  import DecisionCard from './DecisionCard.svelte';
  import TagEditor from './TagEditor.svelte';
  import PersonAvatar from './PersonAvatar.svelte';
  import Skeleton from './Skeleton.svelte';
  import ConfirmModal from './ConfirmModal.svelte';
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
  let speakerEdits = $state({});  // { "SPEAKER_00": "Tom", ... }
  let savingSpeakers = $state(false);

  function openSpeakerEditor() {
    const uniques = [...new Set((transcript?.segments || []).map(s => s.speaker).filter(Boolean))];
    speakerEdits = Object.fromEntries(uniques.map(label => [label, label.startsWith('SPEAKER_') ? '' : label]));
    showSpeakerEditor = true;
  }

  async function saveSpeakerEdits(regenerate = true) {
    savingSpeakers = true;
    try {
      const mapping = {};
      for (const [label, name] of Object.entries(speakerEdits)) {
        if (name && name.trim()) mapping[label] = name.trim();
      }
      if (Object.keys(mapping).length === 0) {
        addToast('No speaker names entered', 'warning');
        return;
      }
      await api.updateTranscriptSpeakers(meetingId, { mapping });
      addToast(`Renamed ${Object.keys(mapping).length} speaker(s)`, 'success');
      showSpeakerEditor = false;
      // Reload transcript to show new labels
      await loadTranscript(meetingId);
      if (regenerate) {
        addToast('Regenerating minutes with new names…', 'info');
        await api.regenerateMeeting(meetingId);
        await loadMeeting(meetingId);
        addToast('Minutes updated', 'success');
      }
    } catch (e) {
      addToast(`Failed to update speakers: ${e.message}`, 'error');
    } finally {
      savingSpeakers = false;
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

  const tabs = $derived([
    { key: 'minutes', label: 'Minutes' },
    { key: 'transcript', label: 'Transcript' },
    { key: 'actions', label: `Actions${meeting?.actions?.length ? ` (${meeting.actions.length})` : ''}` },
    { key: 'decisions', label: `Decisions${meeting?.decisions?.length ? ` (${meeting.decisions.length})` : ''}` },
    { key: 'analytics', label: 'Analytics' }
  ]);

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
        duration: raw.duration,
        participant_sentiments: raw.participant_sentiments || {},
        effectiveness_score: raw.effectiveness_score || 0,
        // Structured minutes fields
        discussion_points: raw.minutes?.discussion_points || [],
        risks_and_concerns: raw.minutes?.risks_and_concerns || [],
        follow_ups: raw.minutes?.follow_ups || [],
        parking_lot: raw.minutes?.parking_lot || [],
        key_topics: raw.minutes?.key_topics || [],
        sentiment: raw.minutes?.sentiment || null,
      };
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
    regenerating = true;
    try {
      await api.regenerateMeeting(meetingId);
      addToast('Minutes regenerated successfully', 'success');
      await loadMeeting(meetingId);
    } catch (e) {
      addToast('Failed to regenerate minutes', 'error');
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
      loadMeeting(id);
      loadTranscript(id);
      loadAnalytics(id);
    }
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
    <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-3">
      {meeting.title || 'Untitled Meeting'}
    </h1>

    <!-- Metadata pills -->
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <MeetingTypeBadge type={meeting.type} />
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
        {#if meeting.minutes_markdown || meeting.summary || meeting.discussion_points?.length}
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
                          Action items ({meeting.actions.length})
                        </h3>
                        <button
                          onclick={() => activeTab = 'actions'}
                          class="text-[11px] text-[var(--accent)] hover:underline"
                        >
                          View all →
                        </button>
                      </div>
                      <ul class="space-y-2">
                        {#each meeting.actions.slice(0, 3) as a}
                          <li class="flex gap-2 text-sm">
                            <span class="text-[var(--text-muted)] mt-0.5">○</span>
                            <div class="flex-1 min-w-0">
                              <span class="text-[var(--text-primary)]">{a.description}</span>
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
              {#if !meeting.summary && !meeting.discussion_points?.length && meeting.minutes_markdown}
                <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
                  <MarkdownRenderer content={meeting.minutes_markdown} />
                </div>
              {/if}
            </div>
          {/if}
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No minutes generated yet.</p>
        {/if}

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
            <div class="mb-4 p-4 bg-[var(--bg-surface)] border border-[var(--accent)] rounded-lg">
              <h4 class="text-sm font-semibold text-[var(--text-primary)] mb-1">Rename speakers</h4>
              <p class="text-xs text-[var(--text-muted)] mb-3">
                Changes are saved to the transcript and minutes will be regenerated with the new names.
              </p>
              <div class="space-y-2 mb-3">
                {#each uniqueSpeakers as label}
                  <div class="flex items-center gap-3">
                    <span class="w-2 h-2 rounded-full shrink-0" style="background-color: {colorFor(label)}"></span>
                    <span class="text-xs font-mono text-[var(--text-muted)] w-24 shrink-0">{label}</span>
                    <input
                      type="text"
                      bind:value={speakerEdits[label]}
                      placeholder="e.g. Tom"
                      class="flex-1 px-2 py-1 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded text-sm text-[var(--text-primary)]
                             focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                    />
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
          <div class="space-y-1">
            {#each meeting.actions as item}
              <ActionItemRow {item} showMeeting={false} />
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
