<script>
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import { browser } from '$app/environment';

  /** @type {{ content: string }} */
  let { content = '' } = $props();

  const rendered = $derived(() => {
    if (!content) return '';
    const raw = marked.parse(content, { breaks: true, gfm: true });
    if (browser) {
      return DOMPurify.sanitize(raw);
    }
    return raw;
  });
</script>

<div class="markdown-content prose prose-sm max-w-none
            prose-headings:text-[var(--text-primary)] prose-headings:font-semibold
            prose-p:text-[var(--text-primary)] prose-p:leading-relaxed
            prose-a:text-[var(--accent)] prose-a:no-underline hover:prose-a:underline
            prose-strong:text-[var(--text-primary)]
            prose-code:text-[var(--accent)] prose-code:bg-[var(--bg-surface-hover)] prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
            prose-pre:bg-[var(--bg-surface-hover)] prose-pre:border prose-pre:border-[var(--border-subtle)] prose-pre:rounded-lg
            prose-li:text-[var(--text-primary)]
            prose-ul:text-[var(--text-primary)]
            prose-ol:text-[var(--text-primary)]
            prose-blockquote:border-[var(--accent)] prose-blockquote:text-[var(--text-secondary)]">
  {@html rendered()}
</div>
