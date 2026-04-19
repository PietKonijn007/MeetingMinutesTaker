<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { theme } from '$lib/stores/theme.js';
  import Skeleton from '$lib/components/Skeleton.svelte';

  // Tab state.
  const tabs = [
    { key: 'meetings', label: 'Meetings' },
    { key: 'commitments', label: 'Commitments' },
    { key: 'topics', label: 'Topics' },
    { key: 'sentiment', label: 'Sentiment' },
    { key: 'effectiveness', label: 'Effectiveness' },
  ];
  let activeTab = $state('meetings');

  // Existing panel 0 (meetings overview) state.
  let stats = $state(null);
  let meetingsOverTime = $state(null);
  let byType = $state(null);
  let actionVelocity = $state(null);
  let loading = $state(true);

  let meetingsChart = $state(null);
  let typeChart = $state(null);
  let velocityChart = $state(null);

  let meetingsCanvas = $state(null);
  let typeCanvas = $state(null);
  let velocityCanvas = $state(null);

  // ANA-1 panel state.
  let commitments = $state(null);
  let commitmentsLoading = $state(false);
  let topics = $state(null);
  let topicsLoading = $state(false);
  let sentiment = $state(null);
  let sentimentLoading = $state(false);
  let sentimentChart = $state(null);
  let sentimentCanvas = $state(null);
  let effectiveness = $state(null);
  let effectivenessLoading = $state(false);
  let effectivenessChart = $state(null);
  let effectivenessCanvas = $state(null);

  async function loadStats() {
    loading = true;
    try {
      const [statsData, meetingsData, typeData, velocityData] = await Promise.allSettled([
        api.getStats(),
        api.getMeetingsOverTime(),
        api.getByType(),
        api.getActionVelocity()
      ]);

      if (statsData.status === 'fulfilled') stats = statsData.value;
      if (meetingsData.status === 'fulfilled') meetingsOverTime = meetingsData.value;
      if (typeData.status === 'fulfilled') byType = typeData.value;
      if (velocityData.status === 'fulfilled') actionVelocity = velocityData.value;
    } catch (e) {
      console.error('Failed to load stats:', e);
    } finally {
      loading = false;
    }
  }

  async function loadCommitments() {
    if (commitments !== null) return;
    commitmentsLoading = true;
    try {
      commitments = await api.getStatsCommitments({ days: 90 });
    } finally {
      commitmentsLoading = false;
    }
  }

  async function loadTopics() {
    if (topics !== null) return;
    topicsLoading = true;
    try {
      topics = await api.getStatsUnresolvedTopics({ min_count: 3 });
    } finally {
      topicsLoading = false;
    }
  }

  async function loadSentiment() {
    if (sentiment !== null) return;
    sentimentLoading = true;
    try {
      sentiment = await api.getStatsSentiment({ days: 90 });
    } finally {
      sentimentLoading = false;
      setTimeout(initSentimentChart, 50);
    }
  }

  async function loadEffectiveness() {
    if (effectiveness !== null) return;
    effectivenessLoading = true;
    try {
      effectiveness = await api.getStatsEffectiveness({});
    } finally {
      effectivenessLoading = false;
      setTimeout(initEffectivenessChart, 50);
    }
  }

  function getChartColors() {
    const isDark = $theme === 'dark';
    return {
      text: isDark ? '#ECECEC' : '#1A1A1A',
      textMuted: isDark ? '#5B5B65' : '#9B9BA5',
      grid: isDark ? '#2E2E32' : '#E8E8EC',
      accent: isDark ? '#818CF8' : '#6366F1',
      accentBg: isDark ? 'rgba(129,140,248,0.15)' : 'rgba(99,102,241,0.1)',
      success: isDark ? '#4ADE80' : '#22C55E',
      danger: isDark ? '#F87171' : '#EF4444'
    };
  }

  async function initCharts() {
    const { Chart, registerables } = await import('chart.js');
    Chart.register(...registerables);

    const colors = getChartColors();

    meetingsChart?.destroy();
    typeChart?.destroy();
    velocityChart?.destroy();

    if (meetingsCanvas && meetingsOverTime) {
      const data = meetingsOverTime.series || [];
      meetingsChart = new Chart(meetingsCanvas, {
        type: 'line',
        data: {
          labels: data.map((d) => d.week),
          datasets: [{
            label: 'Meetings',
            data: data.map((d) => d.count),
            borderColor: colors.accent,
            backgroundColor: colors.accentBg,
            fill: true,
            tension: 0.4,
            pointRadius: 3,
            pointHoverRadius: 5
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { color: colors.grid }, ticks: { color: colors.textMuted, font: { size: 11 } } },
            y: { grid: { color: colors.grid }, ticks: { color: colors.textMuted, font: { size: 11 }, stepSize: 1 }, beginAtZero: true }
          }
        }
      });
    }

    if (typeCanvas && byType) {
      const data = byType.distribution || [];
      const typeColors = {
        standup: '#22C55E', one_on_one: '#0EA5E9', customer_meeting: '#A855F7',
        decision_meeting: '#F59E0B', brainstorm: '#EC4899', retrospective: '#F97316',
        planning: '#14B8A6', other: '#6B7280'
      };
      typeChart = new Chart(typeCanvas, {
        type: 'doughnut',
        data: {
          labels: data.map((d) => d.meeting_type),
          datasets: [{
            data: data.map((d) => d.count),
            backgroundColor: data.map((d) => typeColors[d.meeting_type] || '#6B7280'),
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '65%',
          plugins: {
            legend: { position: 'right', labels: { color: colors.text, font: { size: 11 }, padding: 12 } }
          }
        }
      });
    }

    if (velocityCanvas && actionVelocity) {
      const data = actionVelocity.series || [];
      velocityChart = new Chart(velocityCanvas, {
        type: 'line',
        data: {
          labels: data.map((d) => d.week),
          datasets: [
            {
              label: 'Created',
              data: data.map((d) => d.created),
              borderColor: colors.accent,
              backgroundColor: 'transparent',
              tension: 0.4,
              pointRadius: 3
            },
            {
              label: 'Completed',
              data: data.map((d) => d.completed),
              borderColor: colors.success,
              backgroundColor: 'transparent',
              tension: 0.4,
              pointRadius: 3
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: colors.text, font: { size: 11 } } } },
          scales: {
            x: { grid: { color: colors.grid }, ticks: { color: colors.textMuted, font: { size: 11 } } },
            y: { grid: { color: colors.grid }, ticks: { color: colors.textMuted, font: { size: 11 }, stepSize: 1 }, beginAtZero: true }
          }
        }
      });
    }
  }

  async function initSentimentChart() {
    if (!sentimentCanvas || !sentiment) return;
    const { Chart, registerables } = await import('chart.js');
    Chart.register(...registerables);
    const colors = getChartColors();
    sentimentChart?.destroy();
    const data = sentiment.series || [];
    sentimentChart = new Chart(sentimentCanvas, {
      type: 'line',
      data: {
        labels: data.map(p => p.date?.slice(0, 10) || ''),
        datasets: [{
          label: 'Sentiment',
          data: data.map(p => p.sentiment_score),
          borderColor: colors.accent,
          backgroundColor: colors.accentBg,
          tension: 0.3,
          fill: true,
          pointRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { color: colors.grid }, ticks: { color: colors.textMuted, font: { size: 11 } } },
          y: {
            grid: { color: colors.grid },
            ticks: { color: colors.textMuted, font: { size: 11 } },
            min: 0, max: 1,
          }
        }
      }
    });
  }

  async function initEffectivenessChart() {
    if (!effectivenessCanvas || !effectiveness) return;
    const { Chart, registerables } = await import('chart.js');
    Chart.register(...registerables);
    const colors = getChartColors();
    effectivenessChart?.destroy();
    const rows = effectiveness.types || [];
    effectivenessChart = new Chart(effectivenessCanvas, {
      type: 'bar',
      data: {
        labels: rows.map(r => r.type),
        datasets: [
          { label: 'Clear agenda', data: rows.map(r => (r.had_clear_agenda_pct || 0) * 100), backgroundColor: colors.accent },
          { label: 'Decisions made', data: rows.map(r => (r.decisions_made_pct || 0) * 100), backgroundColor: colors.success },
          { label: 'Actions assigned', data: rows.map(r => (r.action_items_assigned_pct || 0) * 100), backgroundColor: '#F59E0B' },
          { label: 'Unresolved items', data: rows.map(r => (r.unresolved_items_pct || 0) * 100), backgroundColor: colors.danger },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: colors.text, font: { size: 11 } } } },
        scales: {
          x: { grid: { color: colors.grid }, ticks: { color: colors.textMuted, font: { size: 11 } } },
          y: {
            grid: { color: colors.grid },
            ticks: { color: colors.textMuted, font: { size: 11 }, callback: v => `${v}%` },
            min: 0, max: 100,
          }
        }
      }
    });
  }

  function setTab(key) {
    activeTab = key;
    if (key === 'commitments') loadCommitments();
    if (key === 'topics') loadTopics();
    if (key === 'sentiment') loadSentiment();
    if (key === 'effectiveness') loadEffectiveness();
  }

  onMount(async () => {
    await loadStats();
    setTimeout(initCharts, 100);
  });

  // Re-init main charts when theme changes.
  $effect(() => {
    $theme;
    if (!loading && activeTab === 'meetings') {
      setTimeout(initCharts, 50);
    }
    if (activeTab === 'sentiment') setTimeout(initSentimentChart, 50);
    if (activeTab === 'effectiveness') setTimeout(initEffectivenessChart, 50);
  });

  function sparkline(values) {
    if (!values || !values.length) return '';
    const max = Math.max(1, ...values);
    const w = 60;
    const h = 16;
    const step = w / (values.length - 1 || 1);
    const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.4"/>`;
  }
</script>

<div class="max-w-5xl mx-auto">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-6">Stats</h1>

  <!-- Tab bar -->
  <div class="flex gap-1 border-b border-[var(--border-subtle)] mb-6 overflow-x-auto">
    {#each tabs as tab}
      <button
        onclick={() => setTab(tab.key)}
        class="px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition-colors
               {activeTab === tab.key
                 ? 'border-[var(--accent)] text-[var(--accent)]'
                 : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'}"
      >
        {tab.label}
      </button>
    {/each}
  </div>

  {#if activeTab === 'meetings'}
    {#if loading}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        {#each Array(4) as _}
          <Skeleton type="card" />
        {/each}
      </div>
    {:else}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 text-center">
          <div class="text-3xl font-bold text-[var(--text-primary)]">{stats?.total_meetings ?? 0}</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">Total Meetings</div>
        </div>
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 text-center">
          <div class="text-3xl font-bold text-[var(--text-primary)]">{stats?.meetings_this_week ?? 0}</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">This Week</div>
        </div>
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 text-center">
          <div class="text-3xl font-bold text-[var(--text-primary)]">{stats?.open_actions ?? 0}</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">Open Actions</div>
        </div>
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 text-center">
          <div class="text-3xl font-bold text-[var(--text-primary)]">{stats?.avg_duration_minutes ?? 0} min</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">Avg Duration</div>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
          <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Meetings Over Time</h3>
          <div class="h-64"><canvas bind:this={meetingsCanvas}></canvas></div>
        </div>
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
          <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">By Type</h3>
          <div class="h-64"><canvas bind:this={typeCanvas}></canvas></div>
        </div>
        <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 lg:col-span-2">
          <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Action Item Velocity</h3>
          <div class="h-64"><canvas bind:this={velocityCanvas}></canvas></div>
        </div>
      </div>
    {/if}

  {:else if activeTab === 'commitments'}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
      <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Commitment Completion (90 days)</h3>
      {#if commitmentsLoading}
        <Skeleton type="text" lines={4} />
      {:else if commitments?.persons?.length}
        <table class="w-full text-sm">
          <thead class="text-xs uppercase text-[var(--text-muted)]">
            <tr>
              <th class="text-left py-2">Person</th>
              <th class="text-right py-2">Assigned</th>
              <th class="text-right py-2">Completed</th>
              <th class="text-right py-2">Overdue</th>
              <th class="text-right py-2">Rate</th>
              <th class="text-right py-2">12-wk completed</th>
            </tr>
          </thead>
          <tbody>
            {#each commitments.persons as p}
              <tr class="border-t border-[var(--border-subtle)]">
                <td class="py-2 text-[var(--text-primary)]">{p.name}</td>
                <td class="py-2 text-right">{p.assigned}</td>
                <td class="py-2 text-right">{p.completed}</td>
                <td class="py-2 text-right {p.overdue > 0 ? 'text-red-400' : ''}">{p.overdue}</td>
                <td class="py-2 text-right">{(p.completion_rate * 100).toFixed(0)}%</td>
                <td class="py-2 text-right text-[var(--accent)]">
                  <svg viewBox="0 0 60 16" class="inline-block" width="60" height="16">{@html sparkline(p.sparkline)}</svg>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">No action items in this window.</div>
      {/if}
    </div>

  {:else if activeTab === 'topics'}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
      <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Recurring Unresolved Topics</h3>
      {#if topicsLoading}
        <Skeleton type="text" lines={4} />
      {:else if topics?.disabled_reason}
        <div class="text-sm text-[var(--text-muted)]">
          {topics.disabled_reason} — Panel 2 disabled. Other panels still work.
        </div>
      {:else if topics?.clusters?.length}
        <ul class="space-y-3">
          {#each topics.clusters as c}
            <li class="border border-[var(--border-subtle)] rounded-lg p-3 bg-[var(--bg-surface-hover)]">
              <div class="text-[var(--text-primary)] font-medium">{c.topic_summary}</div>
              <div class="text-xs text-[var(--text-muted)] mt-1">
                {c.meeting_count} meetings — first {c.first_mentioned?.slice(0, 10)}, last {c.last_mentioned?.slice(0, 10)}
              </div>
              <div class="flex gap-2 mt-2 flex-wrap">
                {#each c.meeting_ids as mid}
                  <a href="/meeting/{mid}" class="text-xs text-[var(--accent)] hover:underline">
                    {mid.slice(0, 8)}…
                  </a>
                {/each}
              </div>
            </li>
          {/each}
        </ul>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">
          No unresolved topics found with ≥ 3 mentions yet.
        </div>
      {/if}
    </div>

  {:else if activeTab === 'sentiment'}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
      <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Sentiment Over Time</h3>
      {#if sentimentLoading}
        <Skeleton type="text" lines={4} />
      {:else if sentiment?.series?.length}
        <div class="h-64"><canvas bind:this={sentimentCanvas}></canvas></div>
        <div class="text-xs text-[var(--text-muted)] mt-2">
          Positive = 1.0, constructive = 0.7, neutral = 0.5, tense = 0.3, negative = 0.
        </div>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">No sentiment data in this window yet.</div>
      {/if}
    </div>

  {:else if activeTab === 'effectiveness'}
    <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
      <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Meeting-Type Effectiveness</h3>
      {#if effectivenessLoading}
        <Skeleton type="text" lines={4} />
      {:else if effectiveness?.types?.length}
        <div class="h-72"><canvas bind:this={effectivenessCanvas}></canvas></div>
      {:else}
        <div class="text-sm text-[var(--text-muted)]">No effectiveness data yet.</div>
      {/if}
    </div>
  {/if}
</div>
