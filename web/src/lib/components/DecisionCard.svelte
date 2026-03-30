<script>
  /**
   * @type {{
   *   decision: {
   *     id: string,
   *     description: string,
   *     made_by: string,
   *     meeting_id: string,
   *     meeting_title: string,
   *     date: string
   *   }
   * }}
   */
  let { decision } = $props();
</script>

<div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4 hover:bg-[var(--bg-surface-hover)] transition-colors duration-150">
  <p class="text-sm text-[var(--text-primary)] font-medium">
    {decision.description}
  </p>
  <div class="flex items-center gap-2 mt-2 text-xs text-[var(--text-secondary)]">
    {#if decision.made_by}
      <span>Made by: {decision.made_by}</span>
      <span class="text-[var(--text-muted)]">-</span>
    {/if}
    {#if decision.meeting_title}
      <a
        href="/meeting/{decision.meeting_id}"
        class="text-[var(--accent)] hover:underline"
      >
        {decision.meeting_title}
      </a>
    {/if}
  </div>
</div>
