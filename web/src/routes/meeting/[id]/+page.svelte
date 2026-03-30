<script>
  import { page } from '$app/stores';
  import MeetingDetail from '$lib/components/MeetingDetail.svelte';

  const id = $derived($page.params.id);
</script>

<div class="max-w-4xl mx-auto">
  <a href="/" class="inline-flex items-center gap-1.5 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] mb-4 transition-colors">
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
    </svg>
    Back to Calendar
  </a>

  <MeetingDetail meetingId={id} />
</div>
