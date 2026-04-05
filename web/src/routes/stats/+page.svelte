<script>
  import { onMount } from 'svelte';
  import { api } from '$lib/api.js';
  import { theme } from '$lib/stores/theme.js';
  import Skeleton from '$lib/components/Skeleton.svelte';

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

    // Destroy existing charts
    meetingsChart?.destroy();
    typeChart?.destroy();
    velocityChart?.destroy();

    // Meetings over time (area chart)
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

    // By type (donut)
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

    // Action velocity (line)
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

  onMount(async () => {
    await loadStats();
    // Small delay to ensure canvases are rendered
    setTimeout(initCharts, 100);
  });

  // Re-init charts when theme changes
  $effect(() => {
    $theme;
    if (!loading) {
      setTimeout(initCharts, 50);
    }
  });
</script>

<div class="max-w-5xl mx-auto">
  <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-6">Stats</h1>

  {#if loading}
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
      {#each Array(4) as _}
        <Skeleton type="card" />
      {/each}
    </div>
  {:else}
    <!-- Summary cards -->
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

    <!-- Charts -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Meetings over time -->
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
        <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Meetings Over Time</h3>
        <div class="h-64">
          <canvas bind:this={meetingsCanvas}></canvas>
        </div>
      </div>

      <!-- By type -->
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5">
        <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">By Type</h3>
        <div class="h-64">
          <canvas bind:this={typeCanvas}></canvas>
        </div>
      </div>

      <!-- Action velocity -->
      <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg p-5 lg:col-span-2">
        <h3 class="text-sm font-semibold text-[var(--text-primary)] mb-4">Action Item Velocity</h3>
        <div class="h-64">
          <canvas bind:this={velocityCanvas}></canvas>
        </div>
      </div>
    </div>
  {/if}
</div>
