<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { recording } from '$lib/stores/recording.js';
  import StatusStepper from '$lib/components/StatusStepper.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let audioDevices = $state([]);
  let languages = $state([]);
  let selectedDevice = $state('');
  let selectedLanguage = $state('auto');
  let startingRecording = $state(false);
  let stoppingRecording = $state(false);

  const state = $derived($recording.state);
  const elapsedSeconds = $derived($recording.elapsedSeconds);
  const audioLevel = $derived($recording.audioLevel);
  const meetingId = $derived($recording.meetingId);

  function formatElapsed(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  const pipelineSteps = $derived(() => {
    const step = $recording.step;
    const progress = $recording.progress;

    const steps = [
      { label: 'Audio saved', subtitle: '', status: 'done' },
      { label: 'Transcribing', subtitle: '', status: 'pending' },
      { label: 'Generating minutes', subtitle: '', status: 'pending' },
      { label: 'Indexing', subtitle: '', status: 'pending' }
    ];

    if (step === 'transcribing') {
      steps[1].status = 'active';
      steps[1].progress = progress;
    } else if (step === 'generating') {
      steps[1].status = 'done';
      steps[2].status = 'active';
      steps[2].progress = progress;
    } else if (step === 'indexing') {
      steps[1].status = 'done';
      steps[2].status = 'done';
      steps[3].status = 'active';
    } else if (step === 'done' || $recording.state === 'done') {
      steps[1].status = 'done';
      steps[2].status = 'done';
      steps[3].status = 'done';
    }

    return steps;
  });

  // Audio level bars
  const levelBars = $derived(() => {
    const bars = [];
    for (let i = 0; i < 20; i++) {
      const threshold = i / 20;
      const active = audioLevel > threshold;
      bars.push(active);
    }
    return bars;
  });

  async function startRecording() {
    startingRecording = true;
    try {
      const body = {};
      if (selectedDevice) body.audio_device = selectedDevice;
      if (selectedLanguage && selectedLanguage !== 'auto') body.language = selectedLanguage;
      await api.startRecording(body);
      addToast('Recording started', 'success');
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
      addToast('Recording stopped. Processing...', 'info');
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
      audioDevices = data.devices || data || [];
      // Default to first input device
      const inputs = audioDevices.filter(d => d.max_input_channels > 0);
      if (inputs.length > 0 && !selectedDevice) {
        selectedDevice = inputs[0].name;
      }
    } catch (e) {
      // Devices might not be available
    }
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
    api.getRecordingStatus().then(() => {}).catch(() => {});
  });
</script>

<div class="max-w-xl mx-auto">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-8 text-center">Record</h1>

  <!-- Idle state -->
  {#if state === 'idle' || state === 'done'}
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
          <label for="audio-device" class="block text-sm font-medium text-[var(--text-secondary)] mb-2">
            Audio Input Device
          </label>
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

      <!-- Show link to last meeting if done -->
      {#if state === 'done' && meetingId}
        <a
          href="/meeting/{meetingId}"
          class="mt-6 inline-flex items-center gap-2 px-4 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium
                 hover:bg-[var(--accent-hover)] transition-colors"
        >
          View Meeting
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
          </svg>
        </a>
      {/if}
    </div>

  <!-- Recording state -->
  {:else if state === 'recording'}
    <div class="flex flex-col items-center">
      <!-- Pulsing red dot + time -->
      <div class="flex items-center gap-3 mb-6">
        <span class="w-4 h-4 rounded-full bg-red-500 recording-pulse"></span>
        <span class="text-sm font-medium text-red-500">Recording</span>
      </div>

      <div class="text-5xl font-mono font-bold text-[var(--text-primary)] mb-8">
        {formatElapsed(elapsedSeconds)}
      </div>

      <!-- Audio level visualization -->
      <div class="flex items-end gap-0.5 h-12 mb-8">
        {#each levelBars() as active, i}
          <div
            class="w-2 rounded-sm transition-all duration-75
                   {active ? 'bg-[var(--accent)]' : 'bg-[var(--border-subtle)]'}"
            style="height: {Math.max(4, (Math.sin(i * 0.5 + Date.now() * 0.003) + 1) * (active ? 24 : 4))}px"
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

  <!-- Processing state -->
  {:else if state === 'processing'}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-6">
      <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-6">
        Processing meeting{meetingId ? ` ${meetingId.slice(0, 8)}...` : ''}
      </h2>

      <StatusStepper steps={pipelineSteps()} />

      {#if meetingId}
        <div class="mt-6 text-center">
          <a
            href="/meeting/{meetingId}"
            class="text-sm text-[var(--accent)] hover:underline"
          >
            View when ready
          </a>
        </div>
      {/if}
    </div>
  {/if}
</div>
