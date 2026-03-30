<script>
  /** @type {{
   *   year: number,
   *   month: number,
   *   meetingsByDate: Record<string, Array<{id: string, title: string, type: string}>>,
   *   selectedDate: string,
   *   onSelectDate: (date: string) => void,
   *   onPrevMonth: () => void,
   *   onNextMonth: () => void
   * }} */
  let { year, month, meetingsByDate = {}, selectedDate = '', onSelectDate, onPrevMonth, onNextMonth } = $props();

  const typeColors = {
    standup: '#22C55E',
    one_on_one: '#0EA5E9',
    customer_meeting: '#A855F7',
    decision_meeting: '#F59E0B',
    brainstorm: '#EC4899',
    retrospective: '#F97316',
    planning: '#14B8A6',
    other: '#6B7280'
  };

  const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  const monthNames = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
  ];

  const todayStr = $derived(formatDateStr(new Date()));

  const calendarDays = $derived(buildCalendarDays(year, month));

  function formatDateStr(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function buildCalendarDays(y, m) {
    const firstDay = new Date(y, m, 1);
    const startDow = firstDay.getDay(); // 0=Sun
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const daysInPrev = new Date(y, m, 0).getDate();

    const days = [];

    // Previous month fill
    for (let i = startDow - 1; i >= 0; i--) {
      const d = daysInPrev - i;
      const prevMonth = m === 0 ? 11 : m - 1;
      const prevYear = m === 0 ? y - 1 : y;
      days.push({
        day: d,
        dateStr: `${prevYear}-${String(prevMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`,
        currentMonth: false
      });
    }

    // Current month
    for (let d = 1; d <= daysInMonth; d++) {
      days.push({
        day: d,
        dateStr: `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`,
        currentMonth: true
      });
    }

    // Next month fill
    const remaining = 42 - days.length; // 6 rows * 7
    for (let d = 1; d <= remaining; d++) {
      const nextMonth = m === 11 ? 0 : m + 1;
      const nextYear = m === 11 ? y + 1 : y;
      days.push({
        day: d,
        dateStr: `${nextYear}-${String(nextMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`,
        currentMonth: false
      });
    }

    return days;
  }

  function getUniqueMeetingTypes(dateStr) {
    const meetings = meetingsByDate[dateStr];
    if (!meetings || meetings.length === 0) return [];
    const types = [...new Set(meetings.map(m => m.type || 'other'))];
    return types.slice(0, 3); // max 3 dots
  }
</script>

<div class="p-4">
  <!-- Header: < March 2026 > -->
  <div class="flex items-center justify-between mb-4">
    <button
      onclick={onPrevMonth}
      class="p-1.5 rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors duration-150"
      aria-label="Previous month"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
      </svg>
    </button>
    <h2 class="text-sm font-semibold text-[var(--text-primary)]">
      {monthNames[month]} {year}
    </h2>
    <button
      onclick={onNextMonth}
      class="p-1.5 rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors duration-150"
      aria-label="Next month"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
      </svg>
    </button>
  </div>

  <!-- Day names header -->
  <div class="grid grid-cols-7 gap-0 mb-1">
    {#each dayNames as name}
      <div class="text-center text-[10px] font-medium text-[var(--text-muted)] py-1">
        {name}
      </div>
    {/each}
  </div>

  <!-- Day cells -->
  <div class="grid grid-cols-7 gap-0">
    {#each calendarDays as cell}
      {@const isToday = cell.dateStr === todayStr}
      {@const isSelected = cell.dateStr === selectedDate}
      {@const meetingTypes = getUniqueMeetingTypes(cell.dateStr)}
      {@const hasMeetings = meetingTypes.length > 0}
      <button
        onclick={() => onSelectDate(cell.dateStr)}
        class="relative flex flex-col items-center justify-center h-9 rounded-lg text-xs transition-colors duration-150
               {isSelected
                 ? 'bg-[var(--accent)] text-white font-semibold'
                 : isToday
                   ? 'font-semibold text-[var(--accent)]'
                   : cell.currentMonth
                     ? 'text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]'
                     : 'text-[var(--text-muted)] opacity-40 hover:bg-[var(--bg-surface-hover)]'}"
        aria-label="{cell.dateStr}{hasMeetings ? `, ${meetingTypes.length} meeting types` : ''}"
      >
        <span class="{isToday && !isSelected ? 'w-6 h-6 flex items-center justify-center rounded-full ring-2 ring-[var(--accent)]' : ''}">
          {cell.day}
        </span>
        {#if hasMeetings}
          <div class="flex gap-0.5 mt-0.5 absolute bottom-0.5">
            {#each meetingTypes as type}
              <span
                class="w-1 h-1 rounded-full"
                style="background-color: {isSelected ? 'white' : (typeColors[type] || typeColors.other)};"
              ></span>
            {/each}
          </div>
        {/if}
      </button>
    {/each}
  </div>
</div>
