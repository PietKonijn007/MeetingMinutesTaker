<script>
  /**
   * @type {{
   *   icon?: string,
   *   title: string,
   *   description?: string,
   *   ctaLabel?: string,
   *   ctaHref?: string,
   *   onCta?: () => void
   * }}
   */
  let { icon = '', title, description = '', ctaLabel = '', ctaHref = '', onCta } = $props();
</script>

<div class="flex flex-col items-center justify-center py-16 px-4 text-center">
  {#if icon}
    <span class="text-4xl mb-4 opacity-60">{icon}</span>
  {/if}

  <h3 class="text-lg font-semibold text-[var(--text-primary)] mb-2">
    {title}
  </h3>

  {#if description}
    <p class="text-sm text-[var(--text-secondary)] max-w-sm mb-6">
      {description}
    </p>
  {/if}

  {#if ctaLabel}
    {#if ctaHref}
      <a
        href={ctaHref}
        class="inline-flex items-center gap-2 px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium
               hover:bg-[var(--accent-hover)] transition-colors duration-150"
      >
        {ctaLabel}
      </a>
    {:else if onCta}
      <button
        onclick={onCta}
        class="inline-flex items-center gap-2 px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium
               hover:bg-[var(--accent-hover)] transition-colors duration-150"
      >
        {ctaLabel}
      </button>
    {/if}
  {/if}
</div>
