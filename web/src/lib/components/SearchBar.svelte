<script>
  import { browser } from '$app/environment';

  /**
   * @type {{
   *   value?: string,
   *   placeholder?: string,
   *   filters?: Array<{label: string, value: string}>,
   *   onSearch?: (value: string) => void,
   *   onRemoveFilter?: (value: string) => void
   * }}
   */
  let { value = $bindable(''), placeholder = 'Search meetings...', filters = [], onSearch, onRemoveFilter } = $props();

  let inputEl = $state(null);
  let debounceTimer;

  function handleInput(e) {
    const val = e.target.value;
    value = val;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      onSearch?.(val);
    }, 300);
  }

  function handleKeydown(e) {
    if (e.key === 'Escape') {
      value = '';
      onSearch?.('');
      inputEl?.blur();
    }
  }

  // Cmd+K global shortcut
  $effect(() => {
    if (!browser) return;
    function onGlobalKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputEl?.focus();
      }
    }
    window.addEventListener('keydown', onGlobalKey);
    return () => window.removeEventListener('keydown', onGlobalKey);
  });
</script>

<div class="relative w-full">
  <div class="relative">
    <svg
      class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]"
      fill="none" stroke="currentColor" viewBox="0 0 24 24"
    >
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
    </svg>
    <input
      bind:this={inputEl}
      type="text"
      {value}
      oninput={handleInput}
      onkeydown={handleKeydown}
      {placeholder}
      class="w-full pl-10 pr-16 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)]
             rounded-lg text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
             focus:outline-none focus:ring-2 focus:ring-[var(--accent)] focus:border-transparent
             transition-all duration-150"
    />
    <kbd class="absolute right-3 top-1/2 -translate-y-1/2 hidden sm:inline-flex items-center gap-0.5
               px-1.5 py-0.5 bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)]
               rounded text-[10px] text-[var(--text-muted)] font-mono">
      <span class="text-[11px]">&#8984;</span>K
    </kbd>
  </div>

  {#if filters.length > 0}
    <div class="flex items-center gap-2 mt-2 flex-wrap">
      {#each filters as filter}
        <span class="inline-flex items-center gap-1 px-2 py-1 bg-[var(--accent)] bg-opacity-10 text-[var(--accent)] rounded-full text-xs">
          {filter.label}
          <button
            onclick={() => onRemoveFilter?.(filter.value)}
            class="hover:text-[var(--danger)] transition-colors"
            aria-label="Remove filter {filter.label}"
          >
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </span>
      {/each}
    </div>
  {/if}
</div>
