<script>
  /**
   * @type {{
   *   tags?: string[],
   *   onAdd?: (tag: string) => void,
   *   onRemove?: (tag: string) => void
   * }}
   */
  let { tags = $bindable([]), onAdd, onRemove } = $props();

  let adding = $state(false);
  let newTag = $state('');
  let inputEl = $state(null);

  function startAdding() {
    adding = true;
    newTag = '';
    // Focus after DOM update
    setTimeout(() => inputEl?.focus(), 0);
  }

  function addTag() {
    const trimmed = newTag.trim().toLowerCase();
    if (trimmed && !tags.includes(trimmed)) {
      tags = [...tags, trimmed];
      onAdd?.(trimmed);
    }
    newTag = '';
    adding = false;
  }

  function removeTag(tag) {
    tags = tags.filter((t) => t !== tag);
    onRemove?.(tag);
  }

  function handleKeydown(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      addTag();
    } else if (e.key === 'Escape') {
      adding = false;
      newTag = '';
    }
  }
</script>

<div class="flex items-center gap-2 flex-wrap">
  {#each tags as tag}
    <span class="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-secondary)]">
      {tag}
      <button
        onclick={() => removeTag(tag)}
        class="text-[var(--text-muted)] hover:text-[var(--danger)] transition-colors"
        aria-label="Remove tag {tag}"
      >
        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </span>
  {/each}

  {#if adding}
    <input
      bind:this={inputEl}
      bind:value={newTag}
      onkeydown={handleKeydown}
      onblur={addTag}
      placeholder="tag name"
      class="px-2 py-1 bg-transparent border border-[var(--border-subtle)] rounded-full text-xs text-[var(--text-primary)]
             focus:outline-none focus:ring-1 focus:ring-[var(--accent)] w-24"
    />
  {:else}
    <button
      onclick={startAdding}
      class="inline-flex items-center gap-1 px-2.5 py-1 border border-dashed border-[var(--border-subtle)]
             rounded-full text-xs text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)]
             transition-colors duration-150"
    >
      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
      </svg>
      Add tag
    </button>
  {/if}
</div>
