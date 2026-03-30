<script>
  /**
   * @type {{
   *   src: string,
   *   onTimeUpdate?: (currentTime: number) => void
   * }}
   */
  let { src, onTimeUpdate } = $props();

  let audioEl = $state(null);
  let playing = $state(false);
  let currentTime = $state(0);
  let duration = $state(0);
  let volume = $state(1);
  let seeking = $state(false);

  function togglePlay() {
    if (!audioEl) return;
    if (playing) {
      audioEl.pause();
    } else {
      audioEl.play();
    }
  }

  function handleTimeUpdate() {
    if (!seeking && audioEl) {
      currentTime = audioEl.currentTime;
      onTimeUpdate?.(currentTime);
    }
  }

  function handleSeek(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const pct = x / rect.width;
    const newTime = pct * duration;
    if (audioEl) {
      audioEl.currentTime = newTime;
      currentTime = newTime;
    }
  }

  function handleVolumeChange(e) {
    volume = parseFloat(e.target.value);
    if (audioEl) audioEl.volume = volume;
  }

  function formatTime(sec) {
    if (!sec || isNaN(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  const progressPct = $derived(duration > 0 ? (currentTime / duration) * 100 : 0);

  export function seekTo(time) {
    if (audioEl) {
      audioEl.currentTime = time;
      currentTime = time;
    }
  }
</script>

<div class="flex items-center gap-3 p-3 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg">
  <audio
    bind:this={audioEl}
    {src}
    onplay={() => playing = true}
    onpause={() => playing = false}
    ontimeupdate={handleTimeUpdate}
    onloadedmetadata={() => { if (audioEl) duration = audioEl.duration; }}
    onended={() => playing = false}
    preload="metadata"
  ></audio>

  <!-- Play/Pause -->
  <button
    onclick={togglePlay}
    class="shrink-0 w-8 h-8 rounded-full bg-[var(--accent)] text-white flex items-center justify-center
           hover:bg-[var(--accent-hover)] transition-colors duration-150"
    aria-label={playing ? 'Pause' : 'Play'}
  >
    {#if playing}
      <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>
    {:else}
      <svg class="w-4 h-4 ml-0.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
    {/if}
  </button>

  <!-- Time -->
  <span class="text-xs text-[var(--text-secondary)] font-mono w-10 text-right shrink-0">
    {formatTime(currentTime)}
  </span>

  <!-- Seekbar -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="flex-1 h-1.5 bg-[var(--border-subtle)] rounded-full cursor-pointer relative group"
    onclick={handleSeek}
  >
    <div
      class="absolute left-0 top-0 h-full bg-[var(--accent)] rounded-full transition-all duration-75"
      style="width: {progressPct}%"
    ></div>
    <div
      class="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-[var(--accent)] rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
      style="left: calc({progressPct}% - 6px)"
    ></div>
  </div>

  <!-- Duration -->
  <span class="text-xs text-[var(--text-muted)] font-mono w-10 shrink-0">
    {formatTime(duration)}
  </span>

  <!-- Volume -->
  <div class="hidden sm:flex items-center gap-1">
    <svg class="w-4 h-4 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.536 8.464a5 5 0 010 7.072M12 6.253v11.494m0-11.494A5.978 5.978 0 008 4H4v16h4a5.978 5.978 0 004-2.253"/>
    </svg>
    <input
      type="range"
      min="0" max="1" step="0.05"
      value={volume}
      oninput={handleVolumeChange}
      class="w-16 h-1 accent-[var(--accent)]"
      aria-label="Volume"
    />
  </div>
</div>
