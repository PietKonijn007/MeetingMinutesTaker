<script>
  import MeetingTypeBadge from './MeetingTypeBadge.svelte';

  /** @type {{
   *   date: string,
   *   meetings: Array<{id: string, title: string, type: string, duration: number|null, attendees: string[]}>,
   *   selectedMeetingId: string|null,
   *   onSelectMeeting: (id: string) => void
   * }} */
  let { date = '', meetings = [], selectedMeetingId = null, onSelectMeeting, onUpload } = $props();

  const formattedDate = $derived(formatDate(date));

  function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr + 'T12:00:00'); // avoid timezone issues
    return d.toLocaleDateString('en-US', {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric'
    });
  }

  function formatDuration(min) {
    if (!min) return 'N/A';
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    const m = min % 60;
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
</script>

<div class="px-4 pb-4">
  <!-- Date header -->
  <div class="py-3 border-b border-[var(--border-subtle)] mb-2">
    <div class="flex items-center justify-between">
      <h3 class="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">
        Meetings
      </h3>
      {#if onUpload}
        <button
          onclick={() => onUpload(date)}
          class="p-1 rounded-md text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--bg-surface-hover)] transition-colors duration-150"
          title="Upload transcript"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
          </svg>
        </button>
      {/if}
    </div>
    <p class="text-sm font-semibold text-[var(--text-primary)] mt-0.5">
      {formattedDate}
    </p>
  </div>

  {#if meetings.length === 0}
    <div class="py-8 text-center">
      <p class="text-sm text-[var(--text-muted)]">No meetings on this day</p>
    </div>
  {:else}
    <div class="space-y-1">
      {#each meetings as meeting (meeting.id)}
        {@const isSelected = meeting.id === selectedMeetingId}
        <button
          onclick={() => onSelectMeeting(meeting.id)}
          class="w-full text-left p-3 rounded-lg transition-colors duration-150
                 {isSelected
                   ? 'bg-[var(--accent)] bg-opacity-10 border-l-2 border-[var(--accent)] pl-2.5'
                   : 'hover:bg-[var(--bg-surface-hover)] border-l-2 border-transparent pl-2.5'}"
        >
          <div class="flex items-start justify-between gap-2">
            <h4 class="text-sm font-medium text-[var(--text-primary)] truncate flex-1">
              {meeting.title || 'Untitled Meeting'}
            </h4>
          </div>
          <div class="flex items-center gap-2 mt-1.5 flex-wrap">
            <MeetingTypeBadge type={meeting.type} />
            <span class="text-[10px] text-[var(--text-muted)]">
              {formatDuration(meeting.duration)}
            </span>
            {#if meeting.effectiveness_score > 0}
              <span class="text-[10px] inline-flex gap-px" title="Effectiveness: {meeting.effectiveness_score}/5">
                {#each Array(5) as _, i}
                  <span class="{i < meeting.effectiveness_score ? 'text-yellow-400' : 'text-[var(--text-muted)] opacity-30'}">★</span>
                {/each}
              </span>
            {/if}
          </div>
          {#if meeting.attendees?.length}
            <p class="text-[10px] text-[var(--text-muted)] mt-1 truncate">
              {meeting.attendees.slice(0, 3).join(', ')}{meeting.attendees.length > 3 ? ` +${meeting.attendees.length - 3}` : ''}
            </p>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
</div>
