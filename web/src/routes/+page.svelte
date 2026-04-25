<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import CalendarMonth from '$lib/components/CalendarMonth.svelte';
  import DayMeetingList from '$lib/components/DayMeetingList.svelte';
  import MeetingDetail from '$lib/components/MeetingDetail.svelte';
  import MeetingTypeBadge from '$lib/components/MeetingTypeBadge.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import UploadModal from '$lib/components/UploadModal.svelte';

  // Calendar state
  let currentYear = $state(new Date().getFullYear());
  let currentMonth = $state(new Date().getMonth()); // 0-based
  let selectedDate = $state(formatDate(new Date()));
  let selectedMeetingId = $state(null);

  // Data
  let meetingsForMonth = $state([]);
  let loading = $state(true);

  // Upload modal
  let showUploadModal = $state(false);
  let uploadToastMsg = $state('');

  // Search state
  let searchQuery = $state('');
  let searchResults = $state([]);
  let isSearching = $state(false);
  let searchActive = $state(false);
  let searchTimer = null;

  // Derived: group meetings by date string
  const meetingsByDate = $derived(groupByDate(meetingsForMonth));

  // Derived: meetings for the selected day
  const meetingsForDay = $derived(meetingsByDate[selectedDate] || []);

  function formatDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function groupByDate(meetings) {
    const map = {};
    for (const m of meetings) {
      const d = m.date;
      if (!d) continue;
      // Normalize date: take first 10 chars (YYYY-MM-DD)
      const key = d.substring(0, 10);
      if (!map[key]) map[key] = [];
      map[key].push(m);
    }
    return map;
  }

  async function loadMonth() {
    loading = true;
    const after = `${currentYear}-${String(currentMonth + 1).padStart(2, '0')}-01`;
    const lastDay = new Date(currentYear, currentMonth + 1, 0).getDate();
    const before = `${currentYear}-${String(currentMonth + 1).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;

    try {
      const data = await api.getMeetings({ after, before, limit: '100' });
      meetingsForMonth = (data.items || []).map(m => ({
        id: m.meeting_id,
        title: m.title,
        date: m.date,
        type: m.meeting_type,
        duration: m.duration,
        attendees: m.attendee_names || [],
        summary: m.summary,
        action_count: m.action_item_count || 0,
        proposed_action_count: m.proposed_action_count || 0,
        decision_count: m.decision_count || 0,
        effectiveness_score: m.effectiveness_score || 0,
      }));
    } catch (e) {
      console.error('Failed to load meetings:', e);
      meetingsForMonth = [];
    }
    loading = false;

    // Auto-select first meeting of selected day
    autoSelectMeeting();
  }

  function autoSelectMeeting() {
    const dayMeetings = meetingsByDate[selectedDate] || [];
    selectedMeetingId = dayMeetings.length > 0 ? dayMeetings[0].id : null;
  }

  function handleSelectDate(date) {
    selectedDate = date;
    const dayMeetings = meetingsByDate[date] || [];
    selectedMeetingId = dayMeetings.length > 0 ? dayMeetings[0].id : null;
  }

  function handleSelectMeeting(id) {
    selectedMeetingId = id;
  }

  function handleMeetingDeleted(deletedId) {
    // Remove from local data and clear selection
    meetingsForMonth = meetingsForMonth.filter(m => m.id !== deletedId);
    selectedMeetingId = null;
  }

  function handleUploadClick() {
    showUploadModal = true;
  }

  function handleUploadClose() {
    showUploadModal = false;
  }

  function handleUploaded(meetingId) {
    showUploadModal = false;
    uploadToastMsg = 'Transcript uploaded! Processing...';
    // Reload to show the new meeting once pipeline finishes
    loadMonth();
    // Clear toast after a few seconds
    setTimeout(() => { uploadToastMsg = ''; }, 4000);
  }

  function handlePrevMonth() {
    if (currentMonth === 0) {
      currentYear--;
      currentMonth = 11;
    } else {
      currentMonth--;
    }
    loadMonth();
  }

  function handleNextMonth() {
    if (currentMonth === 11) {
      currentYear++;
      currentMonth = 0;
    } else {
      currentMonth++;
    }
    loadMonth();
  }

  // Search functions
  async function handleSearch() {
    if (!searchQuery.trim()) {
      searchActive = false;
      searchResults = [];
      return;
    }
    isSearching = true;
    searchActive = true;
    try {
      const data = await api.search({ q: searchQuery, limit: '50' });
      searchResults = (data.items || []).map(r => ({
        id: r.meeting_id,
        title: r.title,
        date: r.date,
        type: r.meeting_type,
        snippet: r.snippet || '',
      }));
    } catch (e) {
      searchResults = [];
    } finally {
      isSearching = false;
    }
  }

  function clearSearch() {
    searchQuery = '';
    searchActive = false;
    searchResults = [];
    if (searchTimer) clearTimeout(searchTimer);
  }

  function onSearchInput() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(handleSearch, 300);
  }

  function formatSearchDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr.length === 10 ? dateStr + 'T12:00:00' : dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  onMount(() => {
    loadMonth();
  });
</script>

<div class="flex h-[calc(100vh-3.5rem)] -m-6">
  <!-- Left panel: Search + Calendar + Day meeting list -->
  <div class="w-80 shrink-0 border-r border-[var(--border-subtle)] overflow-y-auto bg-[var(--bg-surface)]">
    <!-- Search bar -->
    <div class="px-4 pt-4 pb-2">
      <div class="relative">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
        </svg>
        <input
          bind:value={searchQuery}
          oninput={onSearchInput}
          placeholder="Filter meetings..."
          class="w-full pl-10 pr-8 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
        />
        {#if searchQuery}
          <button onclick={clearSearch} class="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--text-muted)] hover:text-[var(--text-primary)]">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        {/if}
      </div>
    </div>

    {#if loading}
      <div class="p-4 space-y-3">
        <Skeleton type="text" lines={3} />
        <Skeleton type="card" />
      </div>
    {:else}
      <CalendarMonth
        year={currentYear}
        month={currentMonth}
        {meetingsByDate}
        {selectedDate}
        onSelectDate={handleSelectDate}
        onPrevMonth={handlePrevMonth}
        onNextMonth={handleNextMonth}
      />

      <div class="border-t border-[var(--border-subtle)]">
        {#if searchActive}
          <!-- Search results -->
          <div class="px-4 py-2">
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs text-[var(--text-muted)] uppercase tracking-wide">
                Search Results ({searchResults.length})
              </span>
              <button onclick={clearSearch} class="text-xs text-[var(--accent)] hover:underline">Clear</button>
            </div>
            {#if isSearching}
              <p class="text-sm text-[var(--text-muted)] py-4 text-center">Searching...</p>
            {:else if searchResults.length === 0}
              <p class="text-sm text-[var(--text-muted)] py-4 text-center">No results found</p>
            {:else}
              <div class="space-y-2">
                {#each searchResults as result (result.id)}
                  <button
                    onclick={() => { selectedMeetingId = result.id; }}
                    class="w-full text-left p-3 rounded-lg border transition-colors duration-150
                           {selectedMeetingId === result.id
                               ? 'border-[var(--accent)] bg-[var(--accent)]/5'
                               : 'border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)]'}"
                  >
                    <div class="text-sm font-medium text-[var(--text-primary)] truncate">{result.title || 'Untitled'}</div>
                    <div class="flex items-center gap-2 mt-1">
                      <MeetingTypeBadge type={result.type} />
                      <span class="text-xs text-[var(--text-muted)]">{formatSearchDate(result.date)}</span>
                    </div>
                    {#if result.snippet}
                      <p class="text-xs text-[var(--text-secondary)] mt-1 line-clamp-2">{result.snippet}</p>
                    {/if}
                  </button>
                {/each}
              </div>
            {/if}
          </div>
        {:else}
          <DayMeetingList
            date={selectedDate}
            meetings={meetingsForDay}
            {selectedMeetingId}
            onSelectMeeting={handleSelectMeeting}
            onUpload={handleUploadClick}
            onDeleteMeeting={handleMeetingDeleted}
          />
        {/if}
      </div>
    {/if}
  </div>

  <!-- Right panel: Meeting detail -->
  <div class="flex-1 overflow-y-auto">
    {#if selectedMeetingId}
      <MeetingDetail meetingId={selectedMeetingId} onDelete={handleMeetingDeleted} />
    {:else}
      <div class="flex flex-col items-center justify-center h-full text-[var(--text-muted)]">
        <svg class="w-12 h-12 mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
        </svg>
        <p class="text-sm">Select a meeting to view details</p>
      </div>
    {/if}
  </div>
</div>

<UploadModal
  bind:open={showUploadModal}
  date={selectedDate}
  onClose={handleUploadClose}
  onUploaded={handleUploaded}
/>

{#if uploadToastMsg}
  <div class="fixed bottom-4 right-4 z-40 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm shadow-lg">
    {uploadToastMsg}
  </div>
{/if}
