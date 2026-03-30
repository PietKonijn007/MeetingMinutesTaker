<script>
  /** @type {{ type: string }} */
  let { type = 'other' } = $props();

  const typeConfig = {
    standup: { label: 'Standup', color: '#22C55E', bg: 'rgba(34,197,94,0.12)' },
    one_on_one: { label: '1:1', color: '#0EA5E9', bg: 'rgba(14,165,233,0.12)' },
    customer_meeting: { label: 'Customer', color: '#A855F7', bg: 'rgba(168,85,247,0.12)' },
    decision_meeting: { label: 'Decision', color: '#F59E0B', bg: 'rgba(245,158,11,0.12)' },
    brainstorm: { label: 'Brainstorm', color: '#EC4899', bg: 'rgba(236,72,153,0.12)' },
    retrospective: { label: 'Retro', color: '#F97316', bg: 'rgba(249,115,22,0.12)' },
    planning: { label: 'Planning', color: '#14B8A6', bg: 'rgba(20,184,166,0.12)' },
    other: { label: 'Other', color: '#6B7280', bg: 'rgba(107,114,128,0.12)' }
  };

  const config = $derived(typeConfig[type] || typeConfig.other);
</script>

<span
  class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap"
  style="color: {config.color}; background-color: {config.bg};"
>
  {config.label}
</span>
