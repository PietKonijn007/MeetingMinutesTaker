<script>
  import { getMeetingTypeDisplay } from '../meetingTypes.js';

  /** @type {{ type: string }} */
  let { type = 'other' } = $props();

  const config = $derived(getMeetingTypeDisplay(type));
</script>

<span
  class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap"
  style="color: {config.color}; background-color: {config.bg};"
>
  {config.label}
</span>
