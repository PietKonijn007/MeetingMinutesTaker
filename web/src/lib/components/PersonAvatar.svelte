<script>
  /** @type {{ name: string, size?: 'sm' | 'md' | 'lg' }} */
  let { name = '', size = 'md' } = $props();

  const sizeClasses = {
    sm: 'w-7 h-7 text-xs',
    md: 'w-9 h-9 text-sm',
    lg: 'w-12 h-12 text-base'
  };

  function getInitials(name) {
    if (!name) return '?';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0][0].toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function hashColor(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
      hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    const colors = [
      '#6366F1', '#8B5CF6', '#EC4899', '#F43F5E',
      '#F59E0B', '#10B981', '#14B8A6', '#0EA5E9',
      '#3B82F6', '#6D28D9', '#D946EF', '#F97316'
    ];
    return colors[Math.abs(hash) % colors.length];
  }

  const initials = $derived(getInitials(name));
  const bgColor = $derived(hashColor(name));
  const classes = $derived(sizeClasses[size] || sizeClasses.md);
</script>

<div
  class="rounded-full flex items-center justify-center font-semibold text-white shrink-0 {classes}"
  style="background-color: {bgColor};"
  title={name}
  aria-label={name}
>
  {initials}
</div>
