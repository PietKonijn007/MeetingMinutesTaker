<script>
  /**
   * @type {{
   *   lines?: number,
   *   type?: 'card' | 'text' | 'avatar'
   * }}
   */
  let { lines = 3, type = 'text' } = $props();
</script>

{#if type === 'card'}
  <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 space-y-3">
    <div class="skeleton-shimmer h-5 w-3/4 rounded"></div>
    <div class="flex items-center gap-2">
      <div class="skeleton-shimmer h-5 w-16 rounded-full"></div>
      <div class="skeleton-shimmer h-4 w-12 rounded"></div>
      <div class="skeleton-shimmer h-4 w-24 rounded"></div>
    </div>
    <div class="space-y-2">
      <div class="skeleton-shimmer h-3.5 w-full rounded"></div>
      <div class="skeleton-shimmer h-3.5 w-5/6 rounded"></div>
    </div>
    <div class="flex gap-3">
      <div class="skeleton-shimmer h-3.5 w-20 rounded"></div>
      <div class="skeleton-shimmer h-3.5 w-20 rounded"></div>
    </div>
  </div>
{:else if type === 'avatar'}
  <div class="flex items-center gap-3">
    <div class="skeleton-shimmer w-9 h-9 rounded-full shrink-0"></div>
    <div class="space-y-2 flex-1">
      <div class="skeleton-shimmer h-4 w-32 rounded"></div>
      <div class="skeleton-shimmer h-3 w-48 rounded"></div>
    </div>
  </div>
{:else}
  <div class="space-y-2.5">
    {#each Array(lines) as _, i}
      <div
        class="skeleton-shimmer h-3.5 rounded"
        style="width: {i === lines - 1 ? '60%' : `${85 + Math.random() * 15}%`}"
      ></div>
    {/each}
  </div>
{/if}
