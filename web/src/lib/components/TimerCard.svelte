<script>
  import { cancelTimer, dismissTimer } from '../api.js';

  export let timer;
  export let onUpdate = () => {};

  $: remaining = formatRemaining(timer);
  $: firing = timer.state === 'firing';

  function formatRemaining(t) {
    if (t.state === 'firing') return 'FIRING';
    if (!t.remaining_seconds) return '--';
    const m = Math.floor(t.remaining_seconds / 60);
    const s = t.remaining_seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  async function handleCancel() {
    try {
      await cancelTimer(timer.id);
      onUpdate();
    } catch { /* ignore */ }
  }

  async function handleDismiss() {
    try {
      await dismissTimer(timer.id);
      onUpdate();
    } catch { /* ignore */ }
  }
</script>

<div class="bg-bg-card border border-border rounded-lg p-4 flex items-center gap-4
            {firing ? 'border-error animate-timer-flash' : ''}">
  <div class="flex-1">
    <div class="text-sm font-medium text-text-default">{timer.label || 'Timer'}</div>
    <div class="text-2xl font-mono {firing ? 'text-error' : 'text-accent'}">{remaining}</div>
  </div>
  {#if firing}
    <button
      on:click={handleDismiss}
      class="bg-error text-white px-3 py-1.5 rounded text-sm font-bold hover:opacity-90"
    >
      Dismiss
    </button>
  {:else}
    <button
      on:click={handleCancel}
      class="bg-bg-input border border-border text-text-muted px-3 py-1.5 rounded text-sm
             hover:text-error hover:border-error transition-colors"
    >
      Cancel
    </button>
  {/if}
</div>
