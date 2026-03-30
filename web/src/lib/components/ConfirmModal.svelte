<script>
  /**
   * @type {{
   *   open?: boolean,
   *   title?: string,
   *   message?: string,
   *   confirmLabel?: string,
   *   cancelLabel?: string,
   *   danger?: boolean,
   *   onConfirm?: () => void,
   *   onCancel?: () => void
   * }}
   */
  let {
    open = $bindable(false),
    title = 'Confirm',
    message = 'Are you sure?',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    danger = false,
    onConfirm,
    onCancel
  } = $props();

  function handleConfirm() {
    onConfirm?.();
    open = false;
  }

  function handleCancel() {
    onCancel?.();
    open = false;
  }

  function handleKeydown(e) {
    if (e.key === 'Escape') handleCancel();
  }

  function handleBackdropClick(e) {
    if (e.target === e.currentTarget) handleCancel();
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center p-4"
    role="dialog"
    aria-modal="true"
    aria-labelledby="modal-title"
    onkeydown={handleKeydown}
  >
    <!-- Backdrop -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="absolute inset-0 bg-black/50 backdrop-blur-sm"
      onclick={handleBackdropClick}
    ></div>

    <!-- Modal -->
    <div class="relative bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl shadow-xl max-w-md w-full p-6">
      <h3 id="modal-title" class="text-lg font-semibold text-[var(--text-primary)] mb-2">
        {title}
      </h3>
      <p class="text-sm text-[var(--text-secondary)] mb-6">
        {message}
      </p>

      <div class="flex items-center justify-end gap-3">
        <button
          onclick={handleCancel}
          class="px-4 py-2 text-sm font-medium text-[var(--text-secondary)] bg-[var(--bg-surface-hover)]
                 border border-[var(--border-subtle)] rounded-lg
                 hover:text-[var(--text-primary)] transition-colors duration-150"
        >
          {cancelLabel}
        </button>
        <button
          onclick={handleConfirm}
          class="px-4 py-2 text-sm font-medium text-white rounded-lg transition-colors duration-150
                 {danger
                   ? 'bg-[var(--danger)] hover:bg-red-600'
                   : 'bg-[var(--accent)] hover:bg-[var(--accent-hover)]'}"
        >
          {confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}
