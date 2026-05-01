<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { theme } from '$lib/stores/theme.js';
  import { addToast } from '$lib/stores/toasts.js';
  import Skeleton from '$lib/components/Skeleton.svelte';

  let config = $state(null);
  let loading = $state(true);
  let saving = $state(false);
  let audioDevices = $state([]);

  // Local editable state — maps to AppConfig fields
  let recording_device = $state('auto');
  let recording_sample_rate = $state(16000);
  let recording_silence_minutes = $state(5);
  let transcription_engine = $state('whisper');
  let transcription_model = $state('medium');
  let transcription_language = $state('auto');
  let diarization_enabled = $state(true);
  // Diarization backend selection (pluggable architecture)
  let diarization_engine = $state('pyannote');               // pyannote | pyannote-ai | pyannote-mlx
  let diarization_model = $state('pyannote/speaker-diarization-community-1');
  let diarization_pyannote_ai_tier = $state('community-1');  // community-1 | precision-2
  let diarization_pyannote_ai_api_key_env = $state('PYANNOTEAI_API_KEY');
  let diarization_pyannote_ai_api_key_input = $state('');    // not bound to config — write-only
  let diarization_pyannote_ai_key_status = $state({ is_set: false, preview: null });
  let saving_pyannote_key = $state(false);
  let diarization_pyannote_mlx_embedding_model = $state('mlx-community/wespeaker-voxceleb-resnet34-LM');

  // Hardware info & local AI status
  let hardware_info = $state(null);
  let hardware_loading = $state(false);
  let transcription_engines = $state([]);
  let llm_provider = $state('anthropic');
  let llm_model = $state('claude-sonnet-4-6');
  let llm_temperature = $state(0.2);
  let llm_max_tokens = $state(4096);
  let pipeline_mode = $state('automatic');
  let storage_db_path = $state('db/meetings.db');
  let storage_data_dir = $state('~/MeetingMinutesTaker/data');

  // Backup settings
  let backup_enabled = $state(true);
  let backup_dir = $state('backups');
  let backup_interval = $state(1);
  let backups = $state([]);
  let backing_up = $state(false);

  // Retention settings
  let retention_audio_days = $state(90);
  let retention_transcript_days = $state(-1);
  let retention_minutes_days = $state(-1);
  let retention_backup_days = $state(30);
  let retention_status = $state(null);
  let cleaning_up = $state(false);

  // Security settings
  let security_encryption_enabled = $state(false);
  let security_encryption_key = $state('');

  // Performance settings
  let perf_pytorch_mps_fallback = $state(true);
  let generating_key = $state(false);

  // Obsidian settings
  let obsidian_enabled = $state(false);
  let obsidian_vault_path = $state('');
  let testing_obsidian = $state(false);

  // Transcription extras
  let transcription_custom_vocabulary = $state('');

  // LLM extras
  let llm_fallback_provider = $state('');           // '' = disabled
  let llm_fallback_model = $state('');
  let llm_retry_attempts = $state(3);
  let llm_timeout_seconds = $state(120);
  let llm_ollama_base_url = $state('http://localhost:11434');
  let llm_ollama_timeout_seconds = $state(300);

  // Minutes generation (non-LLM)
  let gen_length_mode = $state('concise');
  let gen_generate_email_draft = $state(true);
  let gen_confidentiality_default = $state('auto');
  let gen_close_acknowledged_actions = $state(true);
  let gen_prior_actions_lookback = $state(5);
  let gen_vendors_text = $state('');                // newline-separated
  let gen_templates_dir = $state('templates');

  // Briefing
  let brief_summarize_with_llm = $state(false);

  // Notifications
  let notifications_enabled = $state(null);         // null = auto
  let notifications_sound = $state(true);
  let notifications_click_url_base = $state('http://localhost:8080/meeting');

  // Export
  let export_default_out_dir = $state('data/exports');

  // Advanced (requires service restart)
  let advanced_open = $state(false);
  let log_level = $state('INFO');
  let api_host = $state('127.0.0.1');
  let api_port = $state(8080);
  let api_cors_text = $state('');                   // newline-separated
  let disk_default_planned_minutes = $state(60);
  let disk_flac_compression_factor = $state(0.6);
  let disk_watchdog_interval_seconds = $state(30);
  let disk_watchdog_graceful_stop_factor = $state(0.5);

  // Custom models (successfully used, loaded from API)
  let custom_models = $state({ anthropic: [], openai: [], openrouter: [], ollama: [] });
  let llm_custom_model = $state('');  // text input for typing a custom model

  // Dynamic model list from provider APIs
  let provider_models = $state([]);
  let models_loading = $state(false);
  let models_source = $state('');      // 'api', 'cache', 'fallback'
  let models_warning = $state('');
  let models_fetch_id = $state(0);     // race condition guard

  function formatModelLabel(m) {
    let label = m.name || m.id;
    if (m.pricing) {
      label += `  —  $${m.pricing.prompt} / $${m.pricing.completion} per 1M tokens`;
    }
    if (m.context_length) {
      const ctx = m.context_length >= 1000000
        ? `${(m.context_length / 1000000).toFixed(1)}M`
        : m.context_length >= 1000
          ? `${Math.round(m.context_length / 1000)}K`
          : `${m.context_length}`;
      label += `  —  ${ctx} context`;
    }
    return label;
  }

  function groupModelsByProvider(models) {
    const groups = {};
    for (const m of models) {
      const slash = m.id.indexOf('/');
      const group = slash > 0 ? m.id.substring(0, slash) : 'Other';
      if (!groups[group]) groups[group] = [];
      groups[group].push(m);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }

  async function loadHardwareInfo() {
    hardware_loading = true;
    try {
      hardware_info = await api.request('/config/hardware');
    } catch (_) {
      hardware_info = null;
    } finally {
      hardware_loading = false;
    }
  }

  async function loadTranscriptionEngines() {
    try {
      const result = await api.request('/config/transcription-engines');
      transcription_engines = result.engines || [];
    } catch (_) {
      transcription_engines = [];
    }
  }

  async function loadProviderModels(provider, refresh = false) {
    const fetchId = ++models_fetch_id;
    models_loading = true;
    models_warning = '';
    try {
      const result = await api.getProviderModels(provider, refresh);
      // Guard against race condition if provider changed during fetch
      if (fetchId !== models_fetch_id) return;
      provider_models = result.models || [];
      models_source = result.source || '';
      models_warning = result.warning || '';
    } catch (e) {
      if (fetchId !== models_fetch_id) return;
      provider_models = [];
      models_source = 'error';
      models_warning = e.message;
    } finally {
      if (fetchId === models_fetch_id) {
        models_loading = false;
      }
    }
  }

  async function loadConfig() {
    loading = true;
    try {
      const [cfg, devices] = await Promise.allSettled([
        api.getConfig(),
        api.getAudioDevices()
      ]);

      if (cfg.status === 'fulfilled') {
        config = cfg.value;
        // Map from AppConfig structure: config.config.{section}
        const c = config.config || config || {};
        const r = c.recording || {};
        const t = c.transcription || {};
        const d = c.diarization || {};
        const g = c.generation || {};
        const llm = g.llm || {};
        const p = c.pipeline || {};
        const st = c.storage || {};

        recording_device = r.audio_device || 'auto';
        recording_sample_rate = r.sample_rate || 16000;
        recording_silence_minutes = r.auto_stop_silence_minutes || 5;
        transcription_engine = t.primary_engine || 'whisper';
        transcription_model = t.whisper_model || 'medium';
        transcription_language = t.language || 'auto';
        diarization_enabled = d.enabled !== false;
        diarization_engine = d.engine || 'pyannote';
        diarization_model = d.model || 'pyannote/speaker-diarization-community-1';
        const dai = d.pyannote_ai || {};
        diarization_pyannote_ai_tier = dai.tier || 'community-1';
        diarization_pyannote_ai_api_key_env = dai.api_key_env || 'PYANNOTEAI_API_KEY';
        const dmlx = d.pyannote_mlx || {};
        diarization_pyannote_mlx_embedding_model = dmlx.embedding_model || 'mlx-community/wespeaker-voxceleb-resnet34-LM';
        llm_provider = llm.primary_provider || 'anthropic';
        llm_model = llm.model || 'claude-sonnet-4-6';
        llm_temperature = llm.temperature ?? 0.2;
        llm_max_tokens = llm.max_output_tokens || 4096;
        pipeline_mode = p.mode || 'automatic';
        storage_db_path = st.sqlite_path || 'db/meetings.db';
        storage_data_dir = c.data_dir || '~/MeetingMinutesTaker/data';

        const bk = c.backup || {};
        backup_enabled = bk.enabled !== false;
        backup_dir = bk.backup_dir || 'backups';
        backup_interval = bk.interval_hours || 1;

        const sec = c.security || {};
        security_encryption_enabled = sec.encryption_enabled === true;
        security_encryption_key = sec.encryption_key || '';

        const ob = c.obsidian || {};
        obsidian_enabled = ob.enabled === true;
        obsidian_vault_path = ob.vault_path || '';

        const ret = c.retention || {};
        retention_audio_days = ret.audio_days ?? 90;
        retention_transcript_days = ret.transcript_days ?? -1;
        retention_minutes_days = ret.minutes_days ?? -1;
        retention_backup_days = ret.backup_days ?? 30;

        const perf = c.performance || {};
        perf_pytorch_mps_fallback = perf.pytorch_mps_fallback !== false;

        // Transcription extras
        transcription_custom_vocabulary = t.custom_vocabulary ?? '';

        // LLM extras
        llm_fallback_provider = llm.fallback_provider ?? '';
        llm_fallback_model = llm.fallback_model ?? '';
        llm_retry_attempts = llm.retry_attempts ?? 3;
        llm_timeout_seconds = llm.timeout_seconds ?? 120;
        const ol = llm.ollama || {};
        llm_ollama_base_url = ol.base_url || 'http://localhost:11434';
        llm_ollama_timeout_seconds = ol.timeout_seconds ?? 300;

        // Minutes generation (non-LLM)
        gen_length_mode = g.length_mode || 'concise';
        gen_generate_email_draft = g.generate_email_draft !== false;
        gen_confidentiality_default = g.confidentiality_default || 'auto';
        gen_close_acknowledged_actions = g.close_acknowledged_actions !== false;
        gen_prior_actions_lookback = g.prior_actions_lookback_meetings ?? 5;
        gen_vendors_text = Array.isArray(g.vendors) ? g.vendors.join('\n') : '';
        gen_templates_dir = g.templates_dir || 'templates';

        // Briefing
        const br = c.brief || {};
        brief_summarize_with_llm = br.summarize_with_llm === true;

        // Notifications
        const nt = c.notifications || {};
        notifications_enabled = nt.enabled;           // may be null for auto
        notifications_sound = nt.sound !== false;
        notifications_click_url_base = nt.click_url_base || 'http://localhost:8080/meeting';

        // Export
        const ex = c.export || {};
        export_default_out_dir = ex.default_out_dir || 'data/exports';

        // Advanced
        log_level = c.log_level || 'INFO';
        const ap = c.api || {};
        api_host = ap.host || '127.0.0.1';
        api_port = ap.port || 8080;
        api_cors_text = Array.isArray(ap.cors_origins) ? ap.cors_origins.join('\n') : '';
        const dk = c.disk || {};
        disk_default_planned_minutes = dk.default_planned_minutes ?? 60;
        disk_flac_compression_factor = dk.flac_compression_factor ?? 0.6;
        disk_watchdog_interval_seconds = dk.watchdog_interval_seconds ?? 30;
        disk_watchdog_graceful_stop_factor = dk.watchdog_graceful_stop_factor ?? 0.5;
      }

      // Load custom models
      try {
        custom_models = await api.getCustomModels();
      } catch (_) {
        custom_models = { anthropic: [], openai: [], openrouter: [], ollama: [] };
      }

      // Refresh the pyannoteAI key status — never reveals the value, only
      // whether it's present and a sanitized preview.
      try {
        diarization_pyannote_ai_key_status = await api.getSecret(diarization_pyannote_ai_api_key_env);
      } catch (_) {
        diarization_pyannote_ai_key_status = { is_set: false, preview: null };
      }

      // Load dynamic model list for current provider
      loadProviderModels(llm_provider);

      // Load hardware info and transcription engines
      loadHardwareInfo();
      loadTranscriptionEngines();

      // Load backup list
      try {
        backups = await api.getBackups();
      } catch (_) {
        backups = [];
      }

      // Load retention status
      try {
        retention_status = await api.getRetentionStatus();
      } catch (_) {
        retention_status = null;
      }

      if (devices.status === 'fulfilled') {
        audioDevices = devices.value.devices || devices.value || [];
      }
    } catch (e) {
      console.error('Failed to load config:', e);
    } finally {
      loading = false;
    }
  }

  async function saveConfig() {
    saving = true;
    try {
      const vendors = gen_vendors_text
        .split('\n').map(s => s.trim()).filter(Boolean);
      const cors = api_cors_text
        .split('\n').map(s => s.trim()).filter(Boolean);

      await api.updateConfig({
        data_dir: storage_data_dir,
        log_level: log_level,
        recording: {
          audio_device: recording_device,
          sample_rate: recording_sample_rate,
          auto_stop_silence_minutes: recording_silence_minutes
        },
        transcription: {
          primary_engine: transcription_engine,
          whisper_model: transcription_model,
          language: transcription_language,
          custom_vocabulary: transcription_custom_vocabulary.trim() || null
        },
        diarization: {
          enabled: diarization_enabled,
          engine: diarization_engine,
          model: diarization_model,
          pyannote_ai: {
            tier: diarization_pyannote_ai_tier,
            api_key_env: diarization_pyannote_ai_api_key_env
          },
          pyannote_mlx: {
            embedding_model: diarization_pyannote_mlx_embedding_model
          }
        },
        generation: {
          llm: {
            primary_provider: llm_provider,
            model: llm_model,
            fallback_provider: llm_fallback_provider || null,
            fallback_model: llm_fallback_model.trim() || null,
            temperature: llm_temperature,
            max_output_tokens: llm_max_tokens,
            retry_attempts: llm_retry_attempts,
            timeout_seconds: llm_timeout_seconds,
            ollama: {
              base_url: llm_ollama_base_url,
              timeout_seconds: llm_ollama_timeout_seconds
            }
          },
          templates_dir: gen_templates_dir,
          vendors: vendors,
          length_mode: gen_length_mode,
          generate_email_draft: gen_generate_email_draft,
          confidentiality_default: gen_confidentiality_default,
          close_acknowledged_actions: gen_close_acknowledged_actions,
          prior_actions_lookback_meetings: gen_prior_actions_lookback
        },
        brief: {
          summarize_with_llm: brief_summarize_with_llm
        },
        pipeline: {
          mode: pipeline_mode
        },
        storage: {
          sqlite_path: storage_db_path
        },
        backup: {
          enabled: backup_enabled,
          backup_dir: backup_dir,
          interval_hours: backup_interval
        },
        export: {
          default_out_dir: export_default_out_dir
        },
        obsidian: {
          enabled: obsidian_enabled,
          vault_path: obsidian_vault_path
        },
        notifications: {
          enabled: notifications_enabled,
          sound: notifications_sound,
          click_url_base: notifications_click_url_base
        },
        api: {
          host: api_host,
          port: api_port,
          cors_origins: cors
        },
        security: {
          encryption_enabled: security_encryption_enabled,
          encryption_key: security_encryption_key
        },
        retention: {
          audio_days: retention_audio_days,
          transcript_days: retention_transcript_days,
          minutes_days: retention_minutes_days,
          backup_days: retention_backup_days
        },
        performance: {
          pytorch_mps_fallback: perf_pytorch_mps_fallback
        },
        disk: {
          default_planned_minutes: disk_default_planned_minutes,
          flac_compression_factor: disk_flac_compression_factor,
          watchdog_interval_seconds: disk_watchdog_interval_seconds,
          watchdog_graceful_stop_factor: disk_watchdog_graceful_stop_factor
        }
      });
      addToast('Settings saved', 'success');
    } catch (e) {
      addToast(`Failed to save settings: ${e.message}`, 'error');
    } finally {
      saving = false;
    }
  }

  async function reloadConfig() {
    await loadConfig();
    addToast('Reloaded from config.yaml', 'info');
  }

  async function savePyannoteApiKey() {
    if (!diarization_pyannote_ai_api_key_input.trim()) return;
    saving_pyannote_key = true;
    try {
      const result = await api.setSecret(
        diarization_pyannote_ai_api_key_env,
        diarization_pyannote_ai_api_key_input.trim(),
      );
      diarization_pyannote_ai_api_key_input = '';
      diarization_pyannote_ai_key_status = await api.getSecret(diarization_pyannote_ai_api_key_env);
      const restartNote = result?.restart_required ? ' Restart the server to apply.' : '';
      addToast(`API key saved.${restartNote}`, 'success');
    } catch (e) {
      addToast(`Failed to save API key: ${e.message}`, 'error');
    } finally {
      saving_pyannote_key = false;
    }
  }

  async function clearPyannoteApiKey() {
    saving_pyannote_key = true;
    try {
      await api.clearSecret(diarization_pyannote_ai_api_key_env);
      diarization_pyannote_ai_key_status = { is_set: false, preview: null };
      addToast('API key removed. Restart the server to apply.', 'info');
    } catch (e) {
      addToast(`Failed to clear API key: ${e.message}`, 'error');
    } finally {
      saving_pyannote_key = false;
    }
  }

  onMount(loadConfig);
</script>

<div class="max-w-2xl mx-auto">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-8">Settings</h1>

  {#if loading}
    <div class="space-y-6">
      {#each Array(5) as _}
        <Skeleton type="text" lines={3} />
      {/each}
    </div>
  {:else}
    <div class="space-y-10">

      <!-- Recording -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Recording</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Configure audio capture settings.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Audio Device</label>
            <select
              bind:value={recording_device}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="">Default</option>
              {#each audioDevices as device}
                <option value={device.name || device}>{device.name || device}</option>
              {/each}
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Sample Rate</label>
            <select
              bind:value={recording_sample_rate}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value={16000}>16,000 Hz</option>
              <option value={44100}>44,100 Hz</option>
              <option value={48000}>48,000 Hz</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Auto-stop after silence: {recording_silence_minutes} min
            </label>
            <input
              type="range"
              bind:value={recording_silence_minutes}
              min="1" max="30" step="1"
              class="w-full accent-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Stop recording after this many minutes of silence.</p>
          </div>
        </div>
      </section>

      <!-- Transcription -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Transcription</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Configure local speech-to-text engine.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Engine</label>
            <select
              bind:value={transcription_engine}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="whisper">Faster Whisper (CTranslate2) — GPU-accelerated, best accuracy</option>
              <option value="whisper-cpp">Whisper.cpp (GGML) — faster on CPU, lower memory</option>
            </select>
            {#if transcription_engines.length > 0}
              <div class="mt-1 flex flex-wrap gap-2">
                {#each transcription_engines as eng}
                  <span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full
                    {eng.status === 'installed' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'}">
                    <span class="w-1.5 h-1.5 rounded-full {eng.status === 'installed' ? 'bg-green-500' : 'bg-yellow-500'}"></span>
                    {eng.name}: {eng.status === 'installed' ? 'installed' : 'not installed'}
                  </span>
                {/each}
              </div>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Whisper Model</label>
            <select
              bind:value={transcription_model}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <optgroup label="Standard Models">
                <option value="tiny">tiny (fastest, least accurate)</option>
                <option value="base">base (fast, okay accuracy)</option>
                <option value="small">small (balanced)</option>
                <option value="medium">medium (recommended)</option>
                <option value="large-v3">large-v3 (best accuracy, ~10GB RAM)</option>
              </optgroup>
              <optgroup label="Distil-Whisper (5-6x faster, <1% quality loss)">
                <option value="distil-small.en">distil-small.en (English, very fast)</option>
                <option value="distil-medium.en">distil-medium.en (English, fast + accurate)</option>
                <option value="distil-large-v3">distil-large-v3 (all languages, fast + accurate)</option>
              </optgroup>
            </select>
            {#if hardware_info?.recommendations?.whisper_model}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                Recommended for your hardware: <strong>{hardware_info.recommendations.whisper_model}</strong>
                ({hardware_info.recommendations.whisper_device} / {hardware_info.recommendations.whisper_compute_type})
              </p>
            {:else}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                Distil models are 5-6x faster with &lt;1% quality loss. GPU acceleration is auto-detected.
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Language</label>
            <input
              type="text"
              bind:value={transcription_language}
              placeholder="en"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">ISO 639-1 code (e.g., en, es, fr). Leave blank for auto-detect.</p>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Custom Vocabulary File</label>
            <input
              type="text"
              bind:value={transcription_custom_vocabulary}
              placeholder="(optional) path/to/vocabulary.txt"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Newline-separated custom terms (company names, jargon, acronyms). Leave blank to disable.</p>
          </div>
        </div>
      </section>

      <!-- Speaker ID -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Speaker Identification</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Speaker diarization settings.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={diarization_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable diarization</span>
              <p class="text-xs text-[var(--text-muted)]">Identify and label individual speakers in the transcript.</p>
            </div>
          </label>

          {#if diarization_enabled}
            <div>
              <label for="diariz-engine" class="block text-sm font-medium text-[var(--text-primary)] mb-1">Backend</label>
              <select
                id="diariz-engine"
                bind:value={diarization_engine}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                <option value="pyannote">Local — pyannote (PyTorch, default)</option>
                <option value="pyannote-ai">Cloud — pyannoteAI hosted API (paid)</option>
                <option value="pyannote-mlx">Local — pyannote-mlx (Apple Silicon, experimental)</option>
              </select>
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {#if diarization_engine === 'pyannote'}
                  Runs in-process via PyTorch. Free, private. Slow on long meetings (~0.5–1× realtime).
                {:else if diarization_engine === 'pyannote-ai'}
                  Hosted API by the pyannote.audio authors. Fast (minutes), best DER. Paid (€0.04–€0.11/hr). Requires <code class="text-xs">pip install -e '.[diarize-cloud]'</code>.
                {:else if diarization_engine === 'pyannote-mlx'}
                  Apple Silicon hybrid: pyannote segmentation + MLX embedding. Experimental. Requires <code class="text-xs">pip install -e '.[diarize-mlx]'</code>.
                {/if}
              </p>
            </div>

            {#if diarization_engine === 'pyannote' || diarization_engine === 'pyannote-mlx'}
              <div>
                <label for="diariz-model" class="block text-sm font-medium text-[var(--text-primary)] mb-1">Local model</label>
                <input
                  id="diariz-model"
                  type="text"
                  bind:value={diarization_model}
                  class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                />
                <p class="text-xs text-[var(--text-muted)] mt-1">HuggingFace model name. Default: <code class="text-xs">pyannote/speaker-diarization-community-1</code> (open weights, CC-BY-4.0). Override to <code class="text-xs">pyannote/speaker-diarization-3.1</code> for the legacy model.</p>
              </div>
            {/if}

            {#if diarization_engine === 'pyannote-ai'}
              <div class="border border-[var(--border-subtle)] rounded-lg p-4 space-y-4 bg-[var(--bg-surface)]">
                <div>
                  <label for="diariz-tier" class="block text-sm font-medium text-[var(--text-primary)] mb-1">Tier</label>
                  <select
                    id="diariz-tier"
                    bind:value={diarization_pyannote_ai_tier}
                    class="w-full px-3 py-2 bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  >
                    <option value="community-1">community-1 (~€0.04/hr, open weights)</option>
                    <option value="precision-2">precision-2 (~€0.11/hr, best DER)</option>
                  </select>
                </div>

                <div>
                  <label for="diariz-key-env" class="block text-sm font-medium text-[var(--text-primary)] mb-1">API key env var</label>
                  <input
                    id="diariz-key-env"
                    type="text"
                    bind:value={diarization_pyannote_ai_api_key_env}
                    class="w-full px-3 py-2 bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                  <p class="text-xs text-[var(--text-muted)] mt-1">Name of the environment variable holding the key. Default: <code class="text-xs">PYANNOTEAI_API_KEY</code>.</p>
                </div>

                <div>
                  <label for="diariz-key" class="block text-sm font-medium text-[var(--text-primary)] mb-1">API key</label>
                  {#if diarization_pyannote_ai_key_status.is_set}
                    <div class="flex items-center gap-2 mb-2">
                      <span class="text-xs px-2 py-1 rounded bg-[color-mix(in_srgb,var(--accent)_15%,transparent)] text-[var(--accent)] font-mono">
                        ✓ {diarization_pyannote_ai_key_status.preview ?? 'set'}
                      </span>
                      <button
                        type="button"
                        onclick={clearPyannoteApiKey}
                        disabled={saving_pyannote_key}
                        class="text-xs px-2 py-1 rounded border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-50"
                      >Remove</button>
                    </div>
                  {/if}
                  <div class="flex gap-2">
                    <input
                      id="diariz-key"
                      type="password"
                      bind:value={diarization_pyannote_ai_api_key_input}
                      placeholder={diarization_pyannote_ai_key_status.is_set ? 'Replace key…' : 'Paste your pyannoteAI API key'}
                      autocomplete="off"
                      class="flex-1 px-3 py-2 bg-[var(--bg-base)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                             focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                    />
                    <button
                      type="button"
                      onclick={savePyannoteApiKey}
                      disabled={saving_pyannote_key || !diarization_pyannote_ai_api_key_input.trim()}
                      class="px-3 py-2 bg-[var(--accent)] text-white rounded-lg text-sm font-medium disabled:opacity-50"
                    >{saving_pyannote_key ? 'Saving…' : 'Save key'}</button>
                  </div>
                  <p class="text-xs text-[var(--text-muted)] mt-1">Stored in <code class="text-xs">.env</code> (gitignored, file mode 600). Server restart required to take effect. Get a key at <a href="https://dashboard.pyannote.ai" target="_blank" rel="noopener" class="text-[var(--accent)] hover:underline">dashboard.pyannote.ai</a>.</p>
                </div>
              </div>
            {/if}

            {#if diarization_engine === 'pyannote-mlx'}
              <div>
                <label for="diariz-mlx-model" class="block text-sm font-medium text-[var(--text-primary)] mb-1">MLX embedding model</label>
                <input
                  id="diariz-mlx-model"
                  type="text"
                  bind:value={diarization_pyannote_mlx_embedding_model}
                  class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                />
                <p class="text-xs text-[var(--text-muted)] mt-1">MLX wespeaker port from the mlx-community HuggingFace org. Replaces only the embedding stage; segmentation and clustering still run on PyTorch.</p>
              </div>
            {/if}
          {/if}
        </div>
      </section>

      <!-- Performance & Hardware -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Performance & Hardware</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Hardware acceleration tuning (applied at service startup).</p>

        <label class="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            bind:checked={perf_pytorch_mps_fallback}
            class="mt-0.5 w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                   focus:ring-[var(--accent)] focus:ring-2"
          />
          <div>
            <span class="text-sm font-medium text-[var(--text-primary)]">Enable MPS CPU fallback (Apple Silicon)</span>
            <p class="text-xs text-[var(--text-muted)] mt-0.5">
              Sets <code class="text-[11px] bg-[var(--bg-surface)] px-1 py-0.5 rounded">PYTORCH_ENABLE_MPS_FALLBACK=1</code>.
              When Metal GPU doesn't support an operation, quietly fall back to CPU instead of crashing.
              Recommended on Apple Silicon — ~5–10× faster diarization than pure CPU. No effect on Intel/NVIDIA hardware.
            </p>
            <p class="text-xs text-yellow-500 dark:text-yellow-400 mt-1">
              Requires service restart (<code class="text-[11px]">mm service stop &amp;&amp; mm service start</code>) to take effect.
            </p>
          </div>
        </label>
      </section>

      <!-- Minutes Generation -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Minutes Generation</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">LLM configuration for generating meeting minutes.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">LLM Provider</label>
            <select
              bind:value={llm_provider}
              onchange={() => {
                const defaults = {
                  anthropic: 'claude-sonnet-4-6',
                  openai: 'gpt-4o',
                  openrouter: 'anthropic/claude-sonnet-4',
                  ollama: 'llama3'
                };
                llm_model = defaults[llm_provider] || '';
                llm_custom_model = '';
                loadProviderModels(llm_provider);
              }}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
              <option value="openrouter">OpenRouter</option>
              <option value="ollama">Ollama (local)</option>
            </select>
            {#if llm_provider === 'openrouter'}
              <p class="text-xs text-[var(--text-muted)] mt-1">Access 200+ models from multiple providers via OpenRouter.</p>
            {/if}
          </div>

          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="block text-sm font-medium text-[var(--text-primary)]">Model</label>
              <button
                onclick={() => loadProviderModels(llm_provider, true)}
                disabled={models_loading}
                class="flex items-center gap-1 px-2 py-1 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]
                       transition-colors duration-150 disabled:opacity-50"
                title={llm_provider === 'ollama' ? 'Refresh models from local Ollama' : 'Refresh model list from provider API'}
              >
                <svg class="w-3.5 h-3.5 {models_loading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                {models_loading ? 'Loading...' : 'Refresh'}
              </button>
            </div>

            {#if models_loading}
              <select
                disabled
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-muted)]
                       focus:outline-none opacity-60"
              >
                <option>Loading models from {llm_provider}...</option>
              </select>
            {:else if llm_provider === 'ollama'}
              <!-- Ollama: models fetched from local Ollama instance -->
              {#if provider_models.length > 0}
                <select
                  bind:value={llm_model}
                  class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                >
                  {#each provider_models as m}
                    <option value={m.id}>
                      {m.name}{m.parameter_size ? ` (${m.parameter_size})` : ''}{m.size_gb ? ` — ${m.size_gb}GB` : ''}
                    </option>
                  {/each}
                  {#if custom_models.ollama?.length > 0}
                    <optgroup label="Previously used">
                      {#each custom_models.ollama as m}
                        {#if !provider_models.some(pm => pm.id === m)}
                          <option value={m}>{m}</option>
                        {/if}
                      {/each}
                    </optgroup>
                  {/if}
                </select>
              {:else}
                <select
                  bind:value={llm_model}
                  class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                >
                  {#if custom_models.ollama?.length > 0}
                    {#each custom_models.ollama as m}
                      <option value={m}>{m}</option>
                    {/each}
                  {:else}
                    <option value="" disabled>No models found — is Ollama running?</option>
                  {/if}
                </select>
              {/if}
              {#if hardware_info?.ollama}
                <div class="mt-1 flex items-center gap-2">
                  <span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full
                    {hardware_info.ollama.running ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}">
                    <span class="w-1.5 h-1.5 rounded-full {hardware_info.ollama.running ? 'bg-green-500' : 'bg-red-500'}"></span>
                    Ollama: {hardware_info.ollama.running ? `running (v${hardware_info.ollama.version})` : hardware_info.ollama.installed ? 'installed but not running' : 'not installed'}
                  </span>
                </div>
              {/if}
              {#if hardware_info?.recommendations?.ollama_models?.length > 0}
                <p class="text-xs text-[var(--text-muted)] mt-1">
                  Recommended for your hardware: {hardware_info.recommendations.ollama_models.slice(0, 3).join(', ')}
                </p>
              {/if}
            {:else if provider_models.length > 0 && llm_provider === 'openrouter'}
              <!-- OpenRouter: grouped by provider with pricing -->
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                {#each groupModelsByProvider(provider_models) as [group, models]}
                  <optgroup label={group}>
                    {#each models as m}
                      <option value={m.id}>{formatModelLabel(m)}</option>
                    {/each}
                  </optgroup>
                {/each}
                {#if custom_models.openrouter?.length > 0}
                  <optgroup label="Previously used (custom)">
                    {#each custom_models.openrouter as m}
                      {#if !provider_models.some(pm => pm.id === m)}
                        <option value={m}>{m}</option>
                      {/if}
                    {/each}
                  </optgroup>
                {/if}
              </select>
            {:else if provider_models.length > 0}
              <!-- Anthropic / OpenAI: flat list from API -->
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                {#each provider_models as m}
                  <option value={m.id}>{formatModelLabel(m)}</option>
                {/each}
                {#if custom_models[llm_provider]?.length > 0}
                  <optgroup label="Previously used (custom)">
                    {#each custom_models[llm_provider] as m}
                      {#if !provider_models.some(pm => pm.id === m)}
                        <option value={m}>{m}</option>
                      {/if}
                    {/each}
                  </optgroup>
                {/if}
              </select>
            {:else}
              <!-- Fallback: empty state -->
              <select
                bind:value={llm_model}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                {#if llm_model}
                  <option value={llm_model}>{llm_model}</option>
                {/if}
                <option value="" disabled>Could not load models — enter one below</option>
              </select>
            {/if}

            {#if models_warning}
              <p class="text-xs text-yellow-600 dark:text-yellow-400 mt-1">{models_warning}</p>
            {/if}
            {#if models_source === 'cache' || models_source === 'stale_cache'}
              <p class="text-xs text-[var(--text-muted)] mt-1">Showing cached models. Click Refresh to update.</p>
            {/if}

            <!-- Custom model input for all providers -->
            <div class="mt-2">
              <div class="flex gap-2">
                <input
                  type="text"
                  bind:value={llm_custom_model}
                  placeholder={llm_provider === 'openrouter' ? 'e.g., anthropic/claude-opus-4' : llm_provider === 'ollama' ? 'e.g., llama3' : `Enter a custom ${llm_provider} model ID`}
                  class="flex-1 px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                />
                <button
                  onclick={() => {
                    if (llm_custom_model.trim()) {
                      llm_model = llm_custom_model.trim();
                      llm_custom_model = '';
                    }
                  }}
                  disabled={!llm_custom_model.trim()}
                  class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                         hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150 whitespace-nowrap"
                >
                  Use
                </button>
              </div>
              <p class="text-xs text-[var(--text-muted)] mt-1">
                Type a custom model ID and click Use. It will be saved to the dropdown after a successful generation.
              </p>
            </div>

            {#if llm_model}
              <p class="text-xs text-[var(--text-secondary)] mt-2 font-mono">
                Active model: {llm_model}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Temperature: {llm_temperature.toFixed(1)}
            </label>
            <input
              type="range"
              bind:value={llm_temperature}
              min="0" max="1" step="0.1"
              class="w-full accent-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Max Tokens</label>
            <input
              type="number"
              bind:value={llm_max_tokens}
              min="256" max="32768" step="256"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Retry Attempts</label>
              <input
                type="number"
                bind:value={llm_retry_attempts}
                min="0" max="10" step="1"
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
            </div>
            <div>
              <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Timeout (seconds)</label>
              <input
                type="number"
                bind:value={llm_timeout_seconds}
                min="10" max="600" step="10"
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
            </div>
          </div>

          <div class="pt-2 border-t border-[var(--border-subtle)]">
            <p class="text-sm font-medium text-[var(--text-primary)] mb-2">Fallback Provider</p>
            <p class="text-xs text-[var(--text-muted)] mb-2">Used if the primary provider fails after all retries.</p>
            <div class="grid grid-cols-2 gap-3">
              <select
                bind:value={llm_fallback_provider}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                <option value="">Disabled</option>
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI</option>
                <option value="openrouter">OpenRouter</option>
                <option value="ollama">Ollama (local)</option>
              </select>
              <input
                type="text"
                bind:value={llm_fallback_model}
                placeholder="fallback model id"
                disabled={!llm_fallback_provider}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)] disabled:opacity-50"
              />
            </div>
          </div>

          {#if llm_provider === 'ollama' || llm_fallback_provider === 'ollama'}
            <div class="pt-2 border-t border-[var(--border-subtle)]">
              <p class="text-sm font-medium text-[var(--text-primary)] mb-2">Ollama Server</p>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Base URL</label>
                  <input
                    type="text"
                    bind:value={llm_ollama_base_url}
                    placeholder="http://localhost:11434"
                    class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Timeout (seconds)</label>
                  <input
                    type="number"
                    bind:value={llm_ollama_timeout_seconds}
                    min="30" max="1800" step="30"
                    class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
              <p class="text-xs text-[var(--text-muted)] mt-1">Local models can be slow — give them a generous timeout.</p>
            </div>
          {/if}
        </div>
      </section>

      <!-- Minutes Content -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Minutes Content</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Controls what the generated minutes include and how verbose they are.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Length Mode</label>
            <select
              bind:value={gen_length_mode}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="concise">Concise (~150–400 words, TL;DR-first) — recommended</option>
              <option value="standard">Standard (~400–900 words)</option>
              <option value="verbose">Verbose (~900–1500 words)</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Default Confidentiality</label>
            <select
              bind:value={gen_confidentiality_default}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="auto">Auto (LLM classifies)</option>
              <option value="public">Public</option>
              <option value="internal">Internal</option>
              <option value="confidential">Confidential</option>
              <option value="restricted">Restricted</option>
            </select>
            <p class="text-xs text-[var(--text-muted)] mt-1">Floor for confidentiality labelling on generated minutes.</p>
          </div>

          <label class="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={gen_generate_email_draft}
              class="mt-0.5 w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Generate follow-up email draft</span>
              <p class="text-xs text-[var(--text-muted)]">Emit a ready-to-send email draft section per meeting.</p>
            </div>
          </label>

          <label class="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={gen_close_acknowledged_actions}
              class="mt-0.5 w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Close acknowledged prior actions</span>
              <p class="text-xs text-[var(--text-muted)]">Auto-close open action items from earlier meetings when acknowledged done.</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Prior actions lookback (meetings)</label>
            <input
              type="number"
              bind:value={gen_prior_actions_lookback}
              min="0" max="20" step="1"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">How many previous meetings to scan for open actions.</p>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Vendors</label>
            <textarea
              bind:value={gen_vendors_text}
              rows="3"
              placeholder="AWS&#10;NetApp"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            ></textarea>
            <p class="text-xs text-[var(--text-muted)] mt-1">One vendor name per line. Each gets a service-feedback sub-section in the template.</p>
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Templates Directory</label>
            <input
              type="text"
              bind:value={gen_templates_dir}
              placeholder="templates"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Directory containing .md.j2 Jinja templates.</p>
          </div>
        </div>
      </section>

      <!-- Briefing -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Pre-meeting Briefing</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Settings for the per-meeting briefing page.</p>

        <label class="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            bind:checked={brief_summarize_with_llm}
            class="mt-0.5 w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                   focus:ring-[var(--accent)] focus:ring-2"
          />
          <div>
            <span class="text-sm font-medium text-[var(--text-primary)]">Summarize briefing with LLM</span>
            <p class="text-xs text-[var(--text-muted)]">Run a two-sentence LLM synthesis over the briefing sections. Off by default (DB-only).</p>
          </div>
        </label>
      </section>

      <!-- Pipeline -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Pipeline</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">How recordings are processed.</p>

        <div>
          <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Mode</label>
          <select
            bind:value={pipeline_mode}
            class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
          >
            <option value="automatic">Automatic (process immediately after recording)</option>
            <option value="semi-automatic">Semi-automatic (ask before processing)</option>
            <option value="manual">Manual (process on demand only)</option>
          </select>
        </div>
      </section>

      <!-- Storage -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Storage</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Data storage paths.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Database Path</label>
            <input
              type="text"
              bind:value={storage_db_path}
              placeholder="data/meetings.db"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Data Directory</label>
            <input
              type="text"
              bind:value={storage_data_dir}
              placeholder="data/"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>
        </div>
      </section>

      <!-- Backup -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Database Backups</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Automatic SQLite backups with rotation.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={backup_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable automatic backups</span>
              <p class="text-xs text-[var(--text-muted)]">Create a backup after each pipeline run (rate-limited).</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Backup Directory</label>
            <input
              type="text"
              bind:value={backup_dir}
              placeholder="backups"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Auto-backup interval: {backup_interval} hour{backup_interval !== 1 ? 's' : ''}
            </label>
            <input
              type="range"
              bind:value={backup_interval}
              min="1" max="24" step="1"
              class="w-full accent-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Minimum hours between automatic backups.</p>
          </div>

          <div class="flex items-center gap-3">
            <button
              onclick={async () => {
                backing_up = true;
                try {
                  const result = await api.createBackup();
                  addToast(`Backup created: ${result.filename}`, 'success');
                  backups = await api.getBackups();
                } catch (e) {
                  addToast(`Backup failed: ${e.message}`, 'error');
                } finally {
                  backing_up = false;
                }
              }}
              disabled={backing_up}
              class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                     hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {backing_up ? 'Backing up...' : 'Backup Now'}
            </button>
          </div>

          {#if backups.length > 0}
            <div>
              <p class="text-sm font-medium text-[var(--text-primary)] mb-2">Recent Backups</p>
              <div class="border border-[var(--border-subtle)] rounded-lg overflow-hidden">
                <table class="w-full text-sm">
                  <thead class="bg-[var(--bg-surface)]">
                    <tr>
                      <th class="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Filename</th>
                      <th class="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Size</th>
                      <th class="px-3 py-2 text-left text-[var(--text-muted)] font-medium">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each backups.slice(0, 5) as b}
                      <tr class="border-t border-[var(--border-subtle)]">
                        <td class="px-3 py-2 text-[var(--text-primary)] font-mono text-xs">{b.filename}</td>
                        <td class="px-3 py-2 text-[var(--text-secondary)]">{b.size_mb} MB</td>
                        <td class="px-3 py-2 text-[var(--text-secondary)]">{new Date(b.created).toLocaleString()}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              {#if backups.length > 5}
                <p class="text-xs text-[var(--text-muted)] mt-1">Showing 5 of {backups.length} backups.</p>
              {/if}
            </div>
          {/if}
        </div>
      </section>

      <!-- Data Retention -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Data Retention</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Automatically delete old files. Set to -1 to keep forever.</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Audio recordings: {retention_audio_days === -1 ? 'Keep forever' : `${retention_audio_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_audio_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.audio}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.audio.count} file{retention_status.audio.count !== 1 ? 's' : ''}
                {retention_status.audio.oldest_days != null ? ` (oldest: ${retention_status.audio.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Transcripts: {retention_transcript_days === -1 ? 'Keep forever' : `${retention_transcript_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_transcript_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.transcripts}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.transcripts.count} file{retention_status.transcripts.count !== 1 ? 's' : ''}
                {retention_status.transcripts.oldest_days != null ? ` (oldest: ${retention_status.transcripts.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Minutes: {retention_minutes_days === -1 ? 'Keep forever' : `${retention_minutes_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_minutes_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.minutes}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.minutes.count} file{retention_status.minutes.count !== 1 ? 's' : ''}
                {retention_status.minutes.oldest_days != null ? ` (oldest: ${retention_status.minutes.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">
              Backups: {retention_backup_days === -1 ? 'Keep forever' : `${retention_backup_days} days`}
            </label>
            <input
              type="range"
              bind:value={retention_backup_days}
              min="-1" max="365" step="1"
              class="w-full accent-[var(--accent)]"
            />
            {#if retention_status?.backups}
              <p class="text-xs text-[var(--text-muted)] mt-1">
                {retention_status.backups.count} file{retention_status.backups.count !== 1 ? 's' : ''}
                {retention_status.backups.oldest_days != null ? ` (oldest: ${retention_status.backups.oldest_days} days)` : ''}
              </p>
            {/if}
          </div>

          <div class="flex items-center gap-3">
            <button
              onclick={async () => {
                cleaning_up = true;
                try {
                  await saveConfig();
                  const result = await api.runRetentionCleanup();
                  if (result.total > 0) {
                    addToast(`Cleaned up ${result.total} files`, 'success');
                  } else {
                    addToast('No files to clean up', 'info');
                  }
                  retention_status = await api.getRetentionStatus();
                } catch (e) {
                  addToast(`Cleanup failed: ${e.message}`, 'error');
                } finally {
                  cleaning_up = false;
                }
              }}
              disabled={cleaning_up}
              class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                     hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {cleaning_up ? 'Cleaning up...' : 'Run Cleanup Now'}
            </button>
          </div>
        </div>
      </section>

      <!-- Security -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Security</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Encrypt transcripts and minutes at rest using Fernet symmetric encryption.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={security_encryption_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable encryption at rest</span>
              <p class="text-xs text-[var(--text-muted)]">Encrypt transcript and minutes files after they are written.</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Encryption Key</label>
            <div class="flex gap-2">
              <input
                type="password"
                bind:value={security_encryption_key}
                placeholder="Fernet encryption key"
                class="flex-1 px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              />
              <button
                onclick={async () => {
                  generating_key = true;
                  try {
                    const result = await api.generateEncryptionKey();
                    security_encryption_key = result.key;
                    addToast('Encryption key generated. Save settings to apply.', 'success');
                  } catch (e) {
                    addToast(`Key generation failed: ${e.message}`, 'error');
                  } finally {
                    generating_key = false;
                  }
                }}
                disabled={generating_key}
                class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                       hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150 whitespace-nowrap"
              >
                {generating_key ? 'Generating...' : 'Generate Key'}
              </button>
            </div>
            <p class="text-xs text-[var(--text-muted)] mt-1">
              A Fernet symmetric encryption key. Use the Generate button or run: <code>mm generate-key</code>
            </p>
          </div>

          {#if security_encryption_enabled}
            <div class="p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
              <p class="text-sm text-yellow-800 dark:text-yellow-200 font-medium">Warning</p>
              <p class="text-xs text-yellow-700 dark:text-yellow-300 mt-1">
                Losing the encryption key means losing access to all encrypted data. Store the key securely and keep a backup.
              </p>
            </div>
          {/if}
        </div>
      </section>

      <!-- Obsidian -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Obsidian Export</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Auto-export meeting minutes to an Obsidian vault.</p>

        <div class="space-y-4">
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={obsidian_enabled}
              class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Enable Obsidian export</span>
              <p class="text-xs text-[var(--text-muted)]">Export minutes as Markdown with YAML frontmatter after each pipeline run.</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Vault Path</label>
            <input
              type="text"
              bind:value={obsidian_vault_path}
              placeholder="~/Documents/Obsidian Vault"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Absolute path to your Obsidian vault root folder.</p>
          </div>

          <div class="flex items-center gap-3">
            <button
              onclick={async () => {
                testing_obsidian = true;
                try {
                  // Save config first so the API has the vault path
                  await saveConfig();
                  const result = await api.testObsidian();
                  if (result.success) {
                    addToast(`Test note written: ${result.path}`, 'success');
                  } else {
                    addToast(`Obsidian test failed: ${result.error}`, 'error');
                  }
                } catch (e) {
                  addToast(`Obsidian test failed: ${e.message}`, 'error');
                } finally {
                  testing_obsidian = false;
                }
              }}
              disabled={testing_obsidian || !obsidian_vault_path}
              class="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                     hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
            >
              {testing_obsidian ? 'Testing...' : 'Test Connection'}
            </button>
          </div>
        </div>
      </section>

      <!-- Notifications -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Notifications</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Desktop notifications on pipeline events (macOS only).</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Enabled</label>
            <select
              value={notifications_enabled === null ? 'auto' : notifications_enabled ? 'on' : 'off'}
              onchange={(e) => {
                const v = e.target.value;
                notifications_enabled = v === 'auto' ? null : v === 'on';
              }}
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            >
              <option value="auto">Auto (on for macOS, off elsewhere)</option>
              <option value="on">Always on</option>
              <option value="off">Always off</option>
            </select>
          </div>

          <label class="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={notifications_sound}
              class="mt-0.5 w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                     focus:ring-[var(--accent)] focus:ring-2"
            />
            <div>
              <span class="text-sm font-medium text-[var(--text-primary)]">Play sound</span>
              <p class="text-xs text-[var(--text-muted)]">Play the default notification sound.</p>
            </div>
          </label>

          <div>
            <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Click URL base</label>
            <input
              type="text"
              bind:value={notifications_click_url_base}
              placeholder="http://localhost:8080/meeting"
              class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
            <p class="text-xs text-[var(--text-muted)] mt-1">Meeting ID is appended to this URL when the user clicks a notification.</p>
          </div>
        </div>
      </section>

      <!-- Export -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Export</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">Default output location for CLI exports (PDF, DOCX, Obsidian).</p>

        <div>
          <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Default Output Directory</label>
          <input
            type="text"
            bind:value={export_default_out_dir}
            placeholder="data/exports"
            class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
          />
          <p class="text-xs text-[var(--text-muted)] mt-1">Relative paths resolve against <code class="text-[11px]">data_dir</code>.</p>
        </div>
      </section>

      <!-- Advanced -->
      <section>
        <button
          type="button"
          onclick={() => (advanced_open = !advanced_open)}
          class="w-full flex items-center justify-between text-left"
        >
          <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Advanced</h2>
          <span class="text-sm text-[var(--text-muted)]">{advanced_open ? '▾' : '▸'}</span>
        </button>
        <p class="text-sm text-[var(--text-muted)] mb-4">Infrastructure settings. Changes to server bindings, log level, and disk tuning require a service restart.</p>

        {#if advanced_open}
          <div class="space-y-4 p-4 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg">

            <div>
              <label class="block text-sm font-medium text-[var(--text-primary)] mb-1">Log level</label>
              <select
                bind:value={log_level}
                class="w-full px-3 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              >
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>

            <div class="pt-2 border-t border-[var(--border-subtle)]">
              <p class="text-sm font-medium text-[var(--text-primary)] mb-2">HTTP Server</p>
              <p class="text-xs text-yellow-600 dark:text-yellow-400 mb-2">Changing host/port/CORS requires restarting <code>mm serve</code>.</p>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Host</label>
                  <input
                    type="text"
                    bind:value={api_host}
                    placeholder="127.0.0.1"
                    class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Port</label>
                  <input
                    type="number"
                    bind:value={api_port}
                    min="1" max="65535" step="1"
                    class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
              <div class="mt-2">
                <label class="block text-xs text-[var(--text-muted)] mb-1">CORS origins (one per line)</label>
                <textarea
                  bind:value={api_cors_text}
                  rows="4"
                  placeholder="http://localhost:8080"
                  class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)] font-mono
                         focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                ></textarea>
              </div>
            </div>

            <div class="pt-2 border-t border-[var(--border-subtle)]">
              <p class="text-sm font-medium text-[var(--text-primary)] mb-2">Disk Watchdog</p>
              <p class="text-xs text-[var(--text-muted)] mb-2">Pre-flight disk space + mid-recording watchdog tuning.</p>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Default planned minutes</label>
                  <input
                    type="number"
                    bind:value={disk_default_planned_minutes}
                    min="5" max="480" step="5"
                    class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">FLAC compression factor</label>
                  <input
                    type="number"
                    bind:value={disk_flac_compression_factor}
                    min="0.3" max="1.0" step="0.05"
                    class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Watchdog interval (seconds)</label>
                  <input
                    type="number"
                    bind:value={disk_watchdog_interval_seconds}
                    min="5" max="300" step="5"
                    class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
                <div>
                  <label class="block text-xs text-[var(--text-muted)] mb-1">Graceful-stop factor</label>
                  <input
                    type="number"
                    bind:value={disk_watchdog_graceful_stop_factor}
                    min="0.1" max="1.0" step="0.1"
                    class="w-full px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-subtle)] rounded-lg text-sm text-[var(--text-primary)]
                           focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
            </div>

          </div>
        {/if}
      </section>

      <!-- Appearance -->
      <section>
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-1">Appearance</h2>
        <p class="text-sm text-[var(--text-muted)] mb-4">UI preferences.</p>

        <label class="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={$theme === 'dark'}
            onchange={() => theme.toggle()}
            class="w-4 h-4 rounded border-[var(--border-subtle)] text-[var(--accent)]
                   focus:ring-[var(--accent)] focus:ring-2"
          />
          <div>
            <span class="text-sm font-medium text-[var(--text-primary)]">Dark mode</span>
            <p class="text-xs text-[var(--text-muted)]">Use dark color scheme.</p>
          </div>
        </label>
      </section>

      <!-- Save / Reload buttons -->
      <div class="pt-6 border-t border-[var(--border-subtle)] flex items-center gap-3">
        <button
          onclick={saveConfig}
          disabled={saving}
          class="px-6 py-2.5 bg-[var(--accent)] text-white rounded-lg text-sm font-medium
                 hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-colors duration-150"
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        <button
          onclick={reloadConfig}
          disabled={loading || saving}
          title="Re-read config.yaml from disk (picks up external edits)"
          class="px-4 py-2.5 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg text-sm font-medium text-[var(--text-primary)]
                 hover:bg-[var(--bg-hover)] disabled:opacity-50 transition-colors duration-150"
        >
          Reload from YAML
        </button>
      </div>
    </div>
  {/if}
</div>
