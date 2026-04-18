<script>
  import { onMount, tick } from 'svelte';
  import { api } from '$lib/api.js';
  import MarkdownRenderer from '$lib/components/MarkdownRenderer.svelte';
  import Skeleton from '$lib/components/Skeleton.svelte';
  import { addToast } from '$lib/stores/toasts.js';

  let sessions = $state([]);
  let activeSessionId = $state(null);
  let messages = $state([]);
  let inputText = $state('');
  let loading = $state(false);
  let loadingSessions = $state(true);
  let messagesEndRef = $state(null);

  async function loadSessions() {
    loadingSessions = true;
    try {
      sessions = await api.getChatSessions();
    } catch (_) {
      sessions = [];
    } finally {
      loadingSessions = false;
    }
  }

  async function loadMessages(sessionId) {
    activeSessionId = sessionId;
    try {
      messages = await api.getChatMessages(sessionId);
      await tick();
      scrollToBottom();
    } catch (_) {
      messages = [];
    }
  }

  async function sendMessage() {
    const text = inputText.trim();
    if (!text || loading) return;

    inputText = '';
    loading = true;

    // Show user message immediately
    messages = [...messages, { role: 'user', content: text, citations: [] }];
    await tick();
    scrollToBottom();

    try {
      const result = await api.sendChatMessage(text, activeSessionId);
      activeSessionId = result.session_id;

      // Replace with actual response
      messages = [...messages, {
        role: 'assistant',
        content: result.answer,
        citations: result.citations || [],
      }];

      // Refresh session list
      loadSessions();
    } catch (e) {
      messages = [...messages, {
        role: 'assistant',
        content: `Error: ${e.message}`,
        citations: [],
      }];
    } finally {
      loading = false;
      await tick();
      scrollToBottom();
    }
  }

  function startNewChat() {
    activeSessionId = null;
    messages = [];
    inputText = '';
  }

  async function deleteSession(sessionId) {
    try {
      await api.deleteChatSession(sessionId);
      sessions = sessions.filter(s => s.session_id !== sessionId);
      if (activeSessionId === sessionId) {
        startNewChat();
      }
    } catch (e) {
      addToast('Failed to delete chat', 'error');
    }
  }

  function scrollToBottom() {
    messagesEndRef?.scrollIntoView({ behavior: 'smooth' });
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  onMount(loadSessions);
</script>

<div class="flex h-[calc(100vh-4rem)]">
  <!-- Session sidebar -->
  <div class="w-64 border-r border-[var(--border-subtle)] flex flex-col shrink-0">
    <div class="p-3 border-b border-[var(--border-subtle)]">
      <button
        onclick={startNewChat}
        class="w-full px-3 py-2 bg-[var(--accent)] text-white text-sm font-medium rounded-lg
               hover:opacity-90 transition-opacity flex items-center justify-center gap-2"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
        New chat
      </button>
    </div>

    <div class="flex-1 overflow-y-auto p-2 space-y-1">
      {#if loadingSessions}
        <Skeleton type="text" lines={3} />
      {:else if sessions.length === 0}
        <p class="text-xs text-[var(--text-muted)] text-center py-4">No chats yet</p>
      {:else}
        {#each sessions as s}
          <div class="group flex items-center">
            <button
              onclick={() => loadMessages(s.session_id)}
              class="flex-1 text-left px-3 py-2 rounded-lg text-sm truncate transition-colors
                     {activeSessionId === s.session_id
                       ? 'bg-[var(--accent)] bg-opacity-10 text-[var(--accent)]'
                       : 'text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]'}"
            >
              {s.title || 'Untitled'}
            </button>
            <button
              onclick={() => deleteSession(s.session_id)}
              class="opacity-0 group-hover:opacity-100 p-1 text-[var(--text-muted)] hover:text-[var(--danger)] transition-all"
              title="Delete chat"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        {/each}
      {/if}
    </div>
  </div>

  <!-- Chat area -->
  <div class="flex-1 flex flex-col min-w-0">
    <!-- Messages -->
    <div class="flex-1 overflow-y-auto p-6 space-y-6">
      {#if messages.length === 0 && !loading}
        <div class="flex items-center justify-center h-full">
          <div class="text-center max-w-md">
            <div class="w-16 h-16 mx-auto mb-4 rounded-full bg-[var(--accent)] bg-opacity-10 flex items-center justify-center">
              <svg class="w-8 h-8 text-[var(--accent)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
              </svg>
            </div>
            <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-2">Talk to your meetings</h2>
            <p class="text-sm text-[var(--text-muted)] mb-4">
              Ask questions about your past meetings. I'll search across all your transcripts, minutes, action items, and decisions.
            </p>
            <div class="space-y-2 text-left">
              {#each [
                'Summarize all meetings with Jon Porter since April',
                'What decisions were made about the product roadmap?',
                'Show me open action items for the marketing team',
                'What risks were raised in our last planning session?',
              ] as suggestion}
                <button
                  onclick={() => { inputText = suggestion; }}
                  class="w-full text-left px-3 py-2 text-sm text-[var(--text-secondary)] bg-[var(--bg-surface)]
                         border border-[var(--border-subtle)] rounded-lg
                         hover:border-[var(--accent)] hover:text-[var(--text-primary)] transition-colors"
                >
                  {suggestion}
                </button>
              {/each}
            </div>
          </div>
        </div>
      {:else}
        {#each messages as msg, i}
          <div class="flex {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
            <div class="max-w-[80%] {msg.role === 'user'
              ? 'bg-[var(--accent)] text-white rounded-2xl rounded-br-md px-4 py-3'
              : 'bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl rounded-bl-md px-5 py-4'}">

              {#if msg.role === 'assistant'}
                <div class="prose prose-sm dark:prose-invert max-w-none">
                  <MarkdownRenderer content={msg.content} />
                </div>

                {#if msg.citations?.length}
                  <div class="mt-3 pt-3 border-t border-[var(--border-subtle)]">
                    <p class="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider mb-1.5">Sources</p>
                    <div class="flex flex-wrap gap-1.5">
                      {#each msg.citations as cite}
                        <a
                          href="/meeting/{cite.meeting_id}"
                          class="inline-flex items-center gap-1 px-2 py-0.5 bg-[var(--bg-surface-hover)] rounded text-[11px] text-[var(--accent)] hover:underline"
                        >
                          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                          </svg>
                          {cite.date || ''} {cite.title?.slice(0, 40) || cite.meeting_id.slice(0, 8)}
                        </a>
                      {/each}
                    </div>
                  </div>
                {/if}
              {:else}
                <p class="text-sm">{msg.content}</p>
              {/if}
            </div>
          </div>
        {/each}

        {#if loading}
          <div class="flex justify-start">
            <div class="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl rounded-bl-md px-5 py-4">
              <div class="flex items-center gap-2 text-sm text-[var(--text-muted)]">
                <div class="flex gap-1">
                  <span class="w-2 h-2 bg-[var(--text-muted)] rounded-full animate-bounce" style="animation-delay: 0ms"></span>
                  <span class="w-2 h-2 bg-[var(--text-muted)] rounded-full animate-bounce" style="animation-delay: 150ms"></span>
                  <span class="w-2 h-2 bg-[var(--text-muted)] rounded-full animate-bounce" style="animation-delay: 300ms"></span>
                </div>
                Searching meetings and thinking...
              </div>
            </div>
          </div>
        {/if}

        <div bind:this={messagesEndRef}></div>
      {/if}
    </div>

    <!-- Input -->
    <div class="border-t border-[var(--border-subtle)] p-4">
      <div class="flex gap-3 items-end max-w-3xl mx-auto">
        <textarea
          bind:value={inputText}
          onkeydown={handleKeydown}
          placeholder="Ask about your meetings..."
          rows="1"
          disabled={loading}
          class="flex-1 resize-none px-4 py-3 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl
                 text-sm text-[var(--text-primary)] placeholder-[var(--text-muted)]
                 focus:outline-none focus:ring-2 focus:ring-[var(--accent)] focus:border-transparent
                 disabled:opacity-50"
          oninput={(e) => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'; }}
        ></textarea>
        <button
          onclick={sendMessage}
          disabled={loading || !inputText.trim()}
          class="shrink-0 p-3 bg-[var(--accent)] text-white rounded-xl
                 hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</div>
