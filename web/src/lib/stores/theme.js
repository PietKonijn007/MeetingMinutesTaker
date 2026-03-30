import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function createThemeStore() {
  const getInitialTheme = () => {
    if (!browser) return 'light';
    const stored = localStorage.getItem('theme');
    if (stored) return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  };

  const { subscribe, set, update } = writable(getInitialTheme());

  if (browser) {
    // Watch for system preference changes
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', (e) => {
      if (!localStorage.getItem('theme')) {
        const newTheme = e.matches ? 'dark' : 'light';
        set(newTheme);
        applyTheme(newTheme);
      }
    });

    // Apply on initial load
    subscribe((theme) => {
      applyTheme(theme);
    });
  }

  function applyTheme(theme) {
    if (!browser) return;
    document.documentElement.setAttribute('data-theme', theme);
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }

  return {
    subscribe,
    toggle: () => {
      update((current) => {
        const next = current === 'light' ? 'dark' : 'light';
        if (browser) {
          localStorage.setItem('theme', next);
          applyTheme(next);
        }
        return next;
      });
    },
    set: (theme) => {
      if (browser) {
        localStorage.setItem('theme', theme);
        applyTheme(theme);
      }
      set(theme);
    }
  };
}

export const theme = createThemeStore();
