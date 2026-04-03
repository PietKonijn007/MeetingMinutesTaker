<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import CalendarMonth from '$lib/components/CalendarMonth.svelte';
  import DayMeetingList from '$lib/components/DayMeetingList.svelte';
  import MeetingDetail from '$lib/components/MeetingDetail.svelte';
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
        decision_count: m.decision_count || 0,
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

  onMount(() => {
    loadMonth();
  });
</script>

<div class="flex h-[calc(100vh-3.5rem)] -m-6">
  <!-- Left panel: Calendar + Day meeting list -->
  <div class="w-80 shrink-0 border-r border-[var(--border-subtle)] overflow-y-auto bg-[var(--bg-surface)]">
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
        <DayMeetingList
          date={selectedDate}
          meetings={meetingsForDay}
          {selectedMeetingId}
          onSelectMeeting={handleSelectMeeting}
          onUpload={handleUploadClick}
        />
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
