<script>
  import { remoteAsk } from '../lib/api.js';

  let query = '';
  let loading = false;
  let history = [];

  async function handleAsk() {
    if (!query.trim() || loading) return;
    loading = true;
    const q = query;
    query = '';

    try {
      const result = await remoteAsk(q);
      history = [{ query: q, response: result, error: false }, ...history];
    } catch (e) {
      history = [{ query: q, response: { error: e.message }, error: true }, ...history];
    }
    loading = false;
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  }
</script>

<div class="space-y-6">
  <!-- Query input -->
  <div class="bg-bg-card border border-border rounded-lg p-4">
    <h3 class="text-xs text-text-muted uppercase tracking-wider mb-3">Ask Bender Directly</h3>
    <div class="flex gap-3">
      <input
        type="text"
        bind:value={query}
        on:keydown={handleKeydown}
        placeholder="Ask Bender anything..."
        class="flex-1 bg-bg-input border border-border rounded px-4 py-2 text-text-default
               focus:outline-none focus:border-accent transition-colors"
        disabled={loading}
      />
      <button
        on:click={handleAsk}
        disabled={loading || !query.trim()}
        class="bg-accent text-bg font-bold px-6 py-2 rounded
               hover:opacity-90 transition-opacity disabled:opacity-50"
      >
        {loading ? 'Thinking...' : 'Ask'}
      </button>
    </div>
  </div>

  <!-- Loading state -->
  {#if loading}
    <div class="bg-bg-card border border-border rounded-lg p-8 flex flex-col items-center gap-3">
      <img src="/assets/bender-cigar.png" alt="Thinking..." class="w-16 h-16 object-contain animate-pulse" />
      <p class="text-text-muted text-sm">Bender is thinking...</p>
    </div>
  {/if}

  <!-- Response history -->
  {#each history as item, i}
    <div class="bg-bg-card border border-border rounded-lg p-4 {item.error ? 'border-error' : ''}">
      <div class="text-xs text-text-muted mb-2">You asked:</div>
      <div class="text-text-default mb-3">"{item.query}"</div>

      {#if item.error}
        <div class="flex items-center gap-3">
          <img src="/assets/bender-looking-down.png" alt="Error" class="w-10 h-10 object-contain" />
          <div class="text-error text-sm">{item.response.error || 'Something went wrong'}</div>
        </div>
      {:else}
        <div class="text-xs text-text-muted mb-1">Bender says:</div>
        <div class="text-text-default">{item.response.text || item.response.response || JSON.stringify(item.response)}</div>
        {#if item.response.method}
          <div class="text-xs text-text-muted mt-2">Method: <span class="text-accent">{item.response.method}</span></div>
        {/if}
        {#if item.response.scenario}
          <div class="text-xs text-text-muted">Scenario: <span class="text-accent">{item.response.scenario}</span></div>
        {/if}
      {/if}
    </div>
  {/each}

  <!-- Empty state -->
  {#if !loading && history.length === 0}
    <div class="text-center py-12">
      <img src="/assets/bender-cigar.png" alt="Bender" class="w-24 h-24 mx-auto mb-4 object-contain opacity-50" />
      <p class="text-text-muted text-sm">Ask Bender something. He promises to be mostly helpful.</p>
    </div>
  {/if}
</div>
