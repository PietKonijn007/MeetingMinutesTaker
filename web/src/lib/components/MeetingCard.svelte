<script>
  import MeetingTypeBadge from './MeetingTypeBadge.svelte';

  /**
   * @type {{
   *   meeting: {
   *     id: string,
   *     title: string,
   *     date: string,
   *     type: string,
   *     duration_minutes: number,
   *     attendees: string[],
   *     summary: string,
   *     action_count: number,
   *     decision_count: number
   *   },
   *   view?: 'list' | 'grid'
   * }}
   */
  let { meeting, view = 'list' } = $props();

  function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function formatDuration(min) {
    if (!min) return '';
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    const m = min % 60;
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }

  function truncateAttendees(attendees, max = 3) {
    if (!attendees || attendees.length === 0) return '';
    const shown = attendees.slice(0, max).join(', ');
    const remaining = attendees.length - max;
    return remaining > 0 ? `${shown} +${remaining} more` : shown;
  }

  const isGrid = $derived(view === 'grid');
</script>

<a
  href="/meeting/{meeting.id}"
  class="block bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5
         hover:shadow-md hover:-translate-y-0.5 transition-all duration-150
         {isGrid ? 'flex flex-col' : ''}"
>
  {#if isGrid}
    <div
      class="h-1 -mx-5 -mt-5 mb-4 rounded-t-lg"
      style="background-color: {
        { standup: '#22C55E', one_on_one: '#0EA5E9', customer_meeting: '#A855F7',
          decision_meeting: '#F59E0B', brainstorm: '#EC4899', retrospective: '#F97316',
          planning: '#14B8A6', other: '#6B7280' }[meeting.type] || '#6B7280'
      };"
    ></div>
  {/if}

  <div class="flex items-start justify-between gap-3">
    <h3 class="text-lg font-semibold text-[var(--text-primary)] truncate">
      {meeting.title || 'Untitled Meeting'}
    </h3>
    <span class="text-sm text-[var(--text-secondary)] whitespace-nowrap shrink-0">
      {formatDate(meeting.date)}
    </span>
  </div>

  <div class="flex items-center gap-2 mt-2 flex-wrap">
    <MeetingTypeBadge type={meeting.type} />
    {#if meeting.duration_minutes}
      <span class="text-sm text-[var(--text-secondary)]">{formatDuration(meeting.duration_minutes)}</span>
    {/if}
    {#if meeting.attendees?.length}
      <span class="text-sm text-[var(--text-secondary)]">{truncateAttendees(meeting.attendees)}</span>
    {/if}
  </div>

  {#if meeting.summary}
    <p class="mt-3 text-sm text-[var(--text-secondary)] line-clamp-2">
      {meeting.summary}
    </p>
  {/if}

  <div class="flex items-center gap-4 mt-3">
    {#if meeting.action_count > 0}
      <span class="text-xs text-[var(--text-muted)] flex items-center gap-1">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
        {meeting.action_count} action{meeting.action_count !== 1 ? 's' : ''}
      </span>
    {/if}
    {#if meeting.decision_count > 0}
      <span class="text-xs text-[var(--text-muted)] flex items-center gap-1">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        {meeting.decision_count} decision{meeting.decision_count !== 1 ? 's' : ''}
      </span>
    {/if}
  </div>
</a>
