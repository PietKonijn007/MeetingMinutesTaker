/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{html,js,svelte,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: 'var(--bg-surface)',
        'surface-hover': 'var(--bg-surface-hover)',
        'border-subtle': 'var(--border-subtle)',
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-muted': 'var(--text-muted)',
        accent: 'var(--accent)',
        'accent-hover': 'var(--accent-hover)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        danger: 'var(--danger)',
        meeting: {
          standup: '#22C55E',
          one_on_one: '#0EA5E9',
          customer_meeting: '#A855F7',
          decision_meeting: '#F59E0B',
          brainstorm: '#EC4899',
          retrospective: '#F97316',
          planning: '#14B8A6',
          other: '#6B7280'
        }
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'system-ui', 'sans-serif']
      }
    }
  },
  plugins: []
};
