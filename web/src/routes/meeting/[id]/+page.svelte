<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.js';
  import MeetingTypeBadge from '$lib/components/MeetingTypeBadge.svelte';
  import MarkdownRenderer from '$lib/components/MarkdownRenderer.svelte';
  import AudioPlayer from '$lib/components/AudioPlayer.svelte';
  import ActionItemRow from '$lib/components/ActionItemRow.svelte';
  import DecisionCard from '$lib/components/DecisionCard.svelte';
  import TagEditor from '$lib/components/TagEditor.svelte';
  import PersonAvatar from '$lib/components/PersonAvatar.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import ConfirmModal from '$lib/components/ConfirmModal.svelte';
  import { addToast } from '$lib/components/Toast.svelte';

  let meeting = $state(null);
  let transcript = $state(null);
  let loading = $state(true);
  let activeTab = $state('minutes');
  let showDeleteModal = $state(false);
  let regenerating = $state(false);
  let showAllAttendees = $state(false);
  let currentAudioTime = $state(0);

  let audioPlayerRef = $state(null);

  const id = $derived($page.params.id);

  const tabs = $derived([
    { key: 'minutes', label: 'Minutes' },
    { key: 'transcript', label: 'Transcript' },
    { key: 'actions', label: `Actions${meeting?.actions?.length ? ` (${meeting.actions.length})` : ''}` },
    { key: 'decisions', label: `Decisions${meeting?.decisions?.length ? ` (${meeting.decisions.length})` : ''}` }
  ]);

  function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
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

  async function loadMeeting() {
    loading = true;
    try {
      meeting = await api.getMeeting(id);
    } catch (e) {
      console.error('Failed to load meeting:', e);
      addToast('Failed to load meeting', 'error');
    } finally {
      loading = false;
    }
  }

  async function loadTranscript() {
    try {
      transcript = await api.getTranscript(id);
    } catch (e) {
      console.error('Failed to load transcript:', e);
    }
  }

  async function handleRegenerate() {
    regenerating = true;
    try {
      await api.regenerateMeeting(id);
      addToast('Minutes regenerated successfully', 'success');
      await loadMeeting();
    } catch (e) {
      addToast('Failed to regenerate minutes', 'error');
    } finally {
      regenerating = false;
    }
  }

  async function handleDelete() {
    try {
      await api.deleteMeeting(id);
      addToast('Meeting deleted', 'success');
      goto('/');
    } catch (e) {
      addToast('Failed to delete meeting', 'error');
    }
  }

  async function handleTagsChange(tags) {
    try {
      await api.updateMeeting(id, { tags });
    } catch (e) {
      addToast('Failed to update tags', 'error');
    }
  }

  function seekToTimestamp(seconds) {
    audioPlayerRef?.seekTo(seconds);
  }

  onMount(() => {
    loadMeeting();
    loadTranscript();
  });
</script>

<div class="max-w-4xl mx-auto">
  <!-- Back button -->
  <a
    href="/"
    class="inline-flex items-center gap-1.5 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] mb-4 transition-colors"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
    </svg>
    Back to Meetings
  </a>

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
    </div>

    <!-- Attendees -->
    {#if meeting.attendees?.length}
      <div class="mb-6">
        <div class="flex items-center gap-2 flex-wrap">
          {#each (showAllAttendees ? meeting.attendees : meeting.attendees.slice(0, 5)) as attendee}
            <div class="flex items-center gap-1.5 px-2 py-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-full">
              <PersonAvatar name={attendee} size="sm" />
              <span class="text-xs text-[var(--text-secondary)]">{attendee}</span>
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
        {#if meeting.minutes_markdown}
          <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
            <MarkdownRenderer content={meeting.minutes_markdown} />
          </div>
        {:else}
          <p class="text-sm text-[var(--text-muted)] italic">No minutes generated yet.</p>
        {/if}

      {:else if activeTab === 'transcript'}
        {#if meeting.audio_path}
          <div class="mb-4">
            <AudioPlayer
              bind:this={audioPlayerRef}
              src="/api/meetings/{id}/audio"
              onTimeUpdate={(t) => currentAudioTime = t}
            />
          </div>
        {/if}

        {#if transcript?.segments?.length}
          <div class="space-y-1">
            {#each transcript.segments as segment, i}
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
                  <span class="text-sm font-semibold text-[var(--text-primary)]">{segment.speaker || 'Unknown'}</span>
                  <p class="text-sm text-[var(--text-secondary)] mt-0.5">{segment.text}</p>
                </div>
              </div>
            {/each}
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
