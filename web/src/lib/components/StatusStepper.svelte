<script>
  /**
   * @type {{
   *   steps: Array<{
   *     label: string,
   *     subtitle?: string,
   *     status: 'pending' | 'active' | 'done' | 'error',
   *     progress?: number
   *   }>
   * }}
   */
  let { steps = [] } = $props();
</script>

<div class="space-y-0">
  {#each steps as step, i}
    <div class="flex gap-3 {i < steps.length - 1 ? 'pb-6' : ''}">
      <!-- Icon column -->
      <div class="flex flex-col items-center">
        <div class="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-sm
          {step.status === 'done' ? 'bg-[var(--success)] text-white' :
           step.status === 'active' ? 'bg-[var(--accent)] text-white' :
           step.status === 'error' ? 'bg-[var(--danger)] text-white' :
           'bg-[var(--bg-surface-hover)] text-[var(--text-muted)] border border-[var(--border-subtle)]'}">
          {#if step.status === 'done'}
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/>
            </svg>
          {:else if step.status === 'active'}
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
          {:else if step.status === 'error'}
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          {:else}
            <span class="w-2 h-2 rounded-full bg-current"></span>
          {/if}
        </div>
        {#if i < steps.length - 1}
          <div class="w-0.5 flex-1 mt-1
            {step.status === 'done' ? 'bg-[var(--success)]' : 'bg-[var(--border-subtle)]'}"></div>
        {/if}
      </div>

      <!-- Content -->
      <div class="pt-0.5">
        <p class="text-sm font-medium
          {step.status === 'done' ? 'text-[var(--text-primary)]' :
           step.status === 'active' ? 'text-[var(--text-primary)]' :
           step.status === 'error' ? 'text-[var(--danger)]' :
           'text-[var(--text-muted)]'}">
          {step.label}
        </p>
        {#if step.subtitle}
          <p class="text-xs text-[var(--text-secondary)] mt-0.5">{step.subtitle}</p>
        {/if}
        {#if step.status === 'active' && step.progress != null}
          <div class="mt-2 w-48 h-1.5 bg-[var(--border-subtle)] rounded-full overflow-hidden">
            <div
              class="h-full bg-[var(--accent)] rounded-full transition-all duration-300"
              style="width: {step.progress * 100}%"
            ></div>
          </div>
          <p class="text-xs text-[var(--text-muted)] mt-1">{Math.round(step.progress * 100)}%</p>
        {/if}
      </div>
    </div>
  {/each}
</div>
