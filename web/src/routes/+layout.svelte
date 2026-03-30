<script>
  import '../app.css';
  import { page } from '$app/stores';
  import { theme } from '$lib/stores/theme.js';
  import { recording } from '$lib/stores/recording.js';
  import { onMount } from 'svelte';
  import SearchBar from '$lib/components/SearchBar.svelte';
  import Toast, { toasts } from '$lib/components/Toast.svelte';
  import { goto } from '$app/navigation';

  let { children } = $props();

  let sidebarOpen = $state(true);
  let isMobile = $state(false);

  const navItems = [
    { label: 'Meetings', icon: 'clipboard', href: '/' },
    { label: 'Action Items', icon: 'check', href: '/actions' },
    { label: 'Decisions', icon: 'pin', href: '/decisions' },
    { label: 'People', icon: 'user', href: '/people' },
    { label: 'Stats', icon: 'chart', href: '/stats' }
  ];

  const bottomItems = [
    { label: 'Record', icon: 'record', href: '/record' },
    { label: 'Settings', icon: 'settings', href: '/settings' }
  ];

  function isActive(href, pathname) {
    if (href === '/') return pathname === '/';
    return pathname.startsWith(href);
  }

  function handleSearch(query) {
    if (query) {
      goto(`/?q=${encodeURIComponent(query)}`);
    }
  }

  onMount(() => {
    recording.connect();

    const mq = window.matchMedia('(max-width: 1024px)');
    isMobile = mq.matches;
    if (isMobile) sidebarOpen = false;

    function onResize(e) {
      isMobile = e.matches;
      if (isMobile) sidebarOpen = false;
      else sidebarOpen = true;
    }

    mq.addEventListener('change', onResize);
    return () => {
      recording.disconnect();
      mq.removeEventListener('change', onResize);
    };
  });
</script>

<div class="flex h-screen overflow-hidden">
  <!-- Sidebar -->
  {#if sidebarOpen}
    <!-- Mobile backdrop -->
    {#if isMobile}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="fixed inset-0 bg-black/40 z-30 lg:hidden"
        onclick={() => sidebarOpen = false}
      ></div>
    {/if}

    <aside class="
      {isMobile ? 'fixed inset-y-0 left-0 z-40' : 'relative'}
      w-60 bg-[var(--bg-surface)] border-r border-[var(--border-subtle)]
      flex flex-col shrink-0 overflow-y-auto
    ">
      <!-- Logo area -->
      <div class="px-5 py-4 border-b border-[var(--border-subtle)]">
        <a href="/" class="flex items-center gap-2.5 text-[var(--text-primary)] no-underline">
          <svg class="w-7 h-7" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="6" fill="var(--accent)"/>
            <path d="M8 10h16M8 16h12M8 22h14" stroke="white" stroke-width="2.5" stroke-linecap="round"/>
          </svg>
          <span class="font-semibold text-sm">Meeting Minutes</span>
        </a>
      </div>

      <!-- Main nav -->
      <nav class="flex-1 px-3 py-4 space-y-1">
        {#each navItems as item}
          {@const active = isActive(item.href, $page.url.pathname)}
          <a
            href={item.href}
            onclick={() => { if (isMobile) sidebarOpen = false; }}
            class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-150
                   {active
                     ? 'bg-[var(--accent)] bg-opacity-10 text-[var(--accent)] font-medium border-l-2 border-[var(--accent)] -ml-0.5 pl-[10px]'
                     : 'text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]'}"
          >
            {#if item.icon === 'clipboard'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
            {:else if item.icon === 'check'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            {:else if item.icon === 'pin'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/></svg>
            {:else if item.icon === 'user'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
            {:else if item.icon === 'chart'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
            {/if}
            {item.label}
          </a>
        {/each}

        <hr class="my-4 border-[var(--border-subtle)]" />

        {#each bottomItems as item}
          {@const active = isActive(item.href, $page.url.pathname)}
          <a
            href={item.href}
            onclick={() => { if (isMobile) sidebarOpen = false; }}
            class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors duration-150
                   {active
                     ? 'bg-[var(--accent)] bg-opacity-10 text-[var(--accent)] font-medium border-l-2 border-[var(--accent)] -ml-0.5 pl-[10px]'
                     : 'text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]'}"
          >
            {#if item.icon === 'record'}
              <span class="relative">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-width="2"/><circle cx="12" cy="12" r="4" fill="currentColor"/></svg>
                {#if $recording.state === 'recording'}
                  <span class="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-red-500 recording-pulse"></span>
                {/if}
              </span>
            {:else if item.icon === 'settings'}
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.573-1.066z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
            {/if}
            {item.label}
          </a>
        {/each}
      </nav>
    </aside>
  {/if}

  <!-- Main area -->
  <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
    <!-- Top bar -->
    <header class="h-14 bg-[var(--bg-surface)] border-b border-[var(--border-subtle)] flex items-center gap-4 px-4 shrink-0">
      <!-- Sidebar toggle -->
      <button
        onclick={() => sidebarOpen = !sidebarOpen}
        class="p-1.5 rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors lg:hidden"
        aria-label="Toggle sidebar"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
      </button>

      <button
        onclick={() => sidebarOpen = !sidebarOpen}
        class="p-1.5 rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors hidden lg:block"
        aria-label="Toggle sidebar"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
      </button>

      <!-- Search -->
      <div class="flex-1 max-w-lg">
        <SearchBar placeholder="Search meetings..." onSearch={handleSearch} />
      </div>

      <div class="flex items-center gap-2 ml-auto">
        <!-- Recording indicator -->
        {#if $recording.state === 'recording'}
          <a href="/record" class="flex items-center gap-2 px-2.5 py-1 rounded-full bg-red-500/10 text-red-500 text-xs font-medium">
            <span class="w-2 h-2 rounded-full bg-red-500 recording-pulse"></span>
            Recording
          </a>
        {/if}

        <!-- Dark mode toggle -->
        <button
          onclick={() => theme.toggle()}
          class="p-2 rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors"
          aria-label="Toggle dark mode"
        >
          {#if $theme === 'dark'}
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
          {:else}
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
          {/if}
        </button>

        <!-- Settings -->
        <a
          href="/settings"
          class="p-2 rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors"
          aria-label="Settings"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.573-1.066z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        </a>
      </div>
    </header>

    <!-- Content -->
    <main class="flex-1 overflow-y-auto p-6">
      {@render children()}
    </main>
  </div>
</div>

<!-- Toast container -->
<Toast items={$toasts} />
