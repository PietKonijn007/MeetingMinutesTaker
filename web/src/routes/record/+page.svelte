<script>
  import { onMount, onDestroy } from 'svelte';
  import { api } from '$lib/api.js';
  import { addToast } from '$lib/stores/toasts.js';

  let audioDevices = $state([]);
  let languages = $state([]);
  let selectedDevice = $state('');
  let selectedLanguage = $state('auto');
  let startingRecording = $state(false);
  let stoppingRecording = $state(false);
  let levelHistory = $state(new Array(24).fill(0));
  let refreshingDevices = $state(false);
  let devicePollTimer = $state(null);

  // Recording state — updated by polling /api/recording/status
  let recState = $state('idle');
  let recElapsed = $state(0);
  let recLevel = $state(0);
  let recMeetingId = $state(null);

  // Active pipeline jobs — updated by polling /api/pipelines
  let activePipelines = $state([]);

  let statusPollTimer = null;

  function startStatusPolling() {
    if (statusPollTimer) return;
    statusPollTimer = setInterval(async () => {
      try {
        const status = await api.getRecordingStatus();

        // Don't let stale API responses override local state during transitions
        if (!startingRecording && !stoppingRecording) {
          recState = status.state || 'idle';
        }
        recElapsed = status.elapsed_seconds || 0;
        recLevel = status.audio_level || 0;
        recMeetingId = status.meeting_id || null;

        if (recState === 'recording' && recLevel != null) {
          levelHistory = [...levelHistory.slice(1), recLevel];
        }

        // Also fetch pipeline jobs
        const pipelines = await api.getPipelines();
        activePipelines = pipelines || [];

        // Stop polling once idle and no active pipelines
        if (recState === 'idle' && !startingRecording && activePipelines.length === 0) {
          stopStatusPolling();
        }
      } catch (e) {
        // ignore polling errors
      }
    }, 200); // 5 times/sec for responsive UI
  }

  function stopStatusPolling() {
    if (statusPollTimer) {
      clearInterval(statusPollTimer);
      statusPollTimer = null;
    }
  }

  function formatElapsed(sec) {
    if (sec == null || sec === 0) return '00:00';
    const totalSec = Math.floor(sec);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  async function startRecording() {
    startingRecording = true;
    try {
      const body = {};
      if (selectedDevice) body.audio_device = selectedDevice;
      if (selectedLanguage && selectedLanguage !== 'auto') body.language = selectedLanguage;
      await api.startRecording(body);
      recState = 'recording';
      recElapsed = 0;
      levelHistory = new Array(24).fill(0);
      addToast('Recording started', 'success');
      startStatusPolling();
    } catch (e) {
      addToast(`Failed to start recording: ${e.message}`, 'error');
    } finally {
      startingRecording = false;
    }
  }

  async function stopRecording() {
    stoppingRecording = true;
    try {
      await api.stopRecording();
      // Recording slot is now free — reset local state immediately
      recState = 'idle';
      recMeetingId = null;
      recElapsed = 0;
      levelHistory = new Array(24).fill(0);
      addToast('Recording stopped. Processing in background...', 'info');
      // Keep polling to track pipeline progress
    } catch (e) {
      addToast(`Failed to stop recording: ${e.message}`, 'error');
    } finally {
      stoppingRecording = false;
    }
  }

  let inputDevices = $derived(audioDevices.filter(d => d.max_input_channels > 0));
  let outputDevices = $derived(audioDevices.filter(d => d.max_output_channels > 0));

  async function loadDevices() {
    try {
      const data = await api.getAudioDevices();
      const newDevices = data.devices || data || [];

      // Check if device list actually changed
      const oldNames = audioDevices.map(d => d.name).sort().join(',');
      const newNames = newDevices.map(d => d.name).sort().join(',');
      if (oldNames !== newNames) {
        audioDevices = newDevices;
        // Default to first input device if nothing selected yet
        const inputs = newDevices.filter(d => d.max_input_channels > 0);
        if (inputs.length > 0 && !selectedDevice) {
          selectedDevice = inputs[0].name;
        }
      }
    } catch (e) {
      // Devices might not be available
    }
  }

  async function refreshDevices() {
    refreshingDevices = true;
    await loadDevices();
    refreshingDevices = false;
  }

  async function loadLanguages() {
    try {
      languages = await api.getLanguages();
    } catch (e) {
      languages = [{ code: 'auto', name: 'Auto-detect' }, { code: 'en', name: 'English' }];
    }
  }

  onMount(() => {
    loadDevices();
    loadLanguages();

    // Check initial recording state — resume polling if already recording or pipelines active
    Promise.all([
      api.getRecordingStatus(),
      api.getPipelines(),
    ]).then(([status, pipelines]) => {
      recState = status.state || 'idle';
      recElapsed = status.elapsed_seconds || 0;
      recMeetingId = status.meeting_id || null;
      activePipelines = pipelines || [];
      if (status.state === 'recording' || (pipelines && pipelines.length > 0)) {
        startStatusPolling();
      }
    }).catch(() => {});

    // Poll for new devices every 3 seconds only when idle
    devicePollTimer = setInterval(() => {
      if (recState === 'idle') loadDevices();
    }, 3000);

    return () => {
      if (devicePollTimer) clearInterval(devicePollTimer);
      stopStatusPolling();
    };
  });

  /**
   * Determine step completion status for a pipeline job.
   */
  function stepStatus(job, step) {
    const stepOrder = ['transcribing', 'generating', 'indexing'];
    const stepIdx = stepOrder.indexOf(step);
    const jobIdx = stepOrder.indexOf(job.step);

    if (job.step === 'done' || job.step === 'error') {
      // If done, all steps are done. If error, mark steps up to where it failed.
      return job.step === 'done' ? 'done' : (jobIdx > stepIdx ? 'done' : jobIdx === stepIdx ? 'error' : 'pending');
    }
    if (jobIdx > stepIdx) return 'done';
    if (jobIdx === stepIdx) return 'active';
    return 'pending';
  }
</script>

<div class="max-w-xl mx-auto">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-8 text-center">Record</h1>

  <!-- Idle state — always show record button when not recording -->
  {#if recState === 'idle'}
    <div class="flex flex-col items-center">
      <!-- Big record button -->
      <button
        onclick={startRecording}
        disabled={startingRecording}
        class="w-32 h-32 rounded-full bg-red-500 hover:bg-red-600 text-white
               flex items-center justify-center shadow-lg hover:shadow-xl
               transition-all duration-200 active:scale-95 disabled:opacity-50 mb-8"
        aria-label="Start recording"
      >
        {#if startingRecording}
          <svg class="w-10 h-10 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
          </svg>
        {:else}
          <svg class="w-12 h-12" fill="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="8"/>
          </svg>
        {/if}
      </button>

      <p class="text-sm text-[var(--text-secondary)] mb-8">Click to start recording</p>

      <!-- Device and language selection -->
      <div class="w-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-5">
        <!-- Audio input device -->
        <div>
          <div class="flex items-center justify-between mb-2">
            <label for="audio-device" class="text-sm font-medium text-[var(--text-secondary)]">
              Audio Input Device
            </label>
            <button
              onclick={refreshDevices}
              disabled={refreshingDevices}
              class="text-xs text-[var(--accent)] hover:text-[var(--accent-hover)] transition-colors flex items-center gap-1 disabled:opacity-50"
              title="Refresh device list"
            >
              <svg class="w-3.5 h-3.5 {refreshingDevices ? 'animate-spin' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              Refresh
            </button>
          </div>
          <select
            id="audio-device"
            bind:value={selectedDevice}
            class="w-full px-3 py-2.5 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent transition-shadow"
          >
            {#each inputDevices as device}
              <option value={device.name}>
                {device.name} ({device.max_input_channels}ch input{device.max_output_channels > 0 ? ` + ${device.max_output_channels}ch output` : ''})
              </option>
            {/each}
            {#if inputDevices.length === 0}
              <option value="">No input devices detected</option>
            {/if}
          </select>
          <p class="mt-1.5 text-xs text-[var(--text-muted)]">
            {audioDevices.length} device{audioDevices.length !== 1 ? 's' : ''} detected ({inputDevices.length} with input)
          </p>
        </div>

        <!-- Language -->
        <div>
          <label for="language" class="block text-sm font-medium text-[var(--text-secondary)] mb-2">
            Transcription Language
          </label>
          <select
            id="language"
            bind:value={selectedLanguage}
            class="w-full px-3 py-2.5 bg-[var(--bg-primary)] border border-[var(--border-subtle)] rounded-lg
                   text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]
                   focus:border-transparent transition-shadow"
          >
            {#each languages as lang}
              <option value={lang.code}>{lang.name}</option>
            {/each}
          </select>
          <p class="mt-1.5 text-xs text-[var(--text-muted)]">
            Auto-detect works well for single-language meetings. Select a specific language for better accuracy.
          </p>
        </div>
      </div>
    </div>

  <!-- Recording state -->
  {:else if recState === 'recording'}
    <div class="flex flex-col items-center">
      <!-- Pulsing red dot + time -->
      <div class="flex items-center gap-3 mb-6">
        <span class="w-4 h-4 rounded-full bg-red-500 recording-pulse"></span>
        <span class="text-sm font-medium text-red-500">Recording</span>
      </div>

      <div class="text-5xl font-mono font-bold text-[var(--text-primary)] mb-8">
        {formatElapsed(recElapsed)}
      </div>

      <!-- Audio level visualization — real levels from mic -->
      <div class="flex items-end justify-center gap-1 h-16 mb-8">
        {#each levelHistory as level, i}
          {@const barHeight = Math.max(3, level * 60)}
          {@const isActive = level > 0.02}
          <div
            class="w-2 rounded-full transition-all duration-100"
            style="height: {barHeight}px; background-color: {isActive ? 'var(--accent)' : 'var(--border-subtle)'}; opacity: {isActive ? 0.5 + level * 0.5 : 0.3}"
          ></div>
        {/each}
      </div>

      <!-- Stop button -->
      <div class="flex items-center gap-4">
        <button
          onclick={stopRecording}
          disabled={stoppingRecording}
          class="px-8 py-3 bg-red-500 hover:bg-red-600 text-white rounded-full
                 text-sm font-medium shadow-lg transition-all duration-150 disabled:opacity-50
                 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2"/>
          </svg>
          {stoppingRecording ? 'Stopping...' : 'Stop Recording'}
        </button>
      </div>
    </div>
  {/if}

  <!-- Active Pipelines — always shown below recording controls -->
  {#if activePipelines.length > 0}
    <div class="mt-8 w-full">
      <h3 class="text-sm font-medium text-[var(--text-secondary)] mb-3">Processing</h3>
      {#each activePipelines as job}
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-4 mb-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-sm font-medium text-[var(--text-primary)]">
              Meeting {job.meeting_id.slice(0, 8)}...
            </span>
            <div class="flex items-center gap-3">
              <span class="text-xs text-[var(--text-muted)]">
                {Math.round(job.elapsed_seconds)}s
              </span>
              {#if job.step === 'done'}
                <a
                  href="/meeting/{job.meeting_id}"
                  class="text-xs text-[var(--accent)] hover:underline"
                >
                  View
                </a>
              {/if}
            </div>
          </div>
          <!-- Step indicator -->
          <div class="flex items-center gap-2 text-xs">
            {#each ['transcribing', 'generating', 'indexing'] as step, i}
              {@const status = stepStatus(job, step)}
              <div class="flex items-center gap-1">
                {#if status === 'done'}
                  <span class="text-green-500">&#10003;</span>
                {:else if status === 'active'}
                  <span class="text-[var(--accent)] animate-pulse">&#9203;</span>
                {:else if status === 'error'}
                  <span class="text-red-500">&#10007;</span>
                {:else}
                  <span class="text-[var(--text-muted)]">&#9675;</span>
                {/if}
                <span class="{status === 'active' ? 'text-[var(--accent)] font-medium' : status === 'done' ? 'text-green-500' : status === 'error' ? 'text-red-500' : 'text-[var(--text-muted)]'}">
                  {step === 'transcribing' ? 'Transcribe' : step === 'generating' ? 'Generate' : 'Index'}
                </span>
              </div>
              {#if i < 2}
                <span class="text-[var(--text-muted)]">&rarr;</span>
              {/if}
            {/each}
            {#if job.step === 'done'}
              <span class="text-green-500 font-medium ml-2">&#10003; Done</span>
            {:else if job.step === 'error'}
              <span class="text-red-500 font-medium ml-2">&#10007; Error</span>
            {/if}
          </div>
          {#if job.step === 'error' && job.error}
            <p class="mt-2 text-xs text-red-500 truncate" title={job.error}>{job.error}</p>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>
