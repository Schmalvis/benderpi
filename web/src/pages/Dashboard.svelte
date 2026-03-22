<script>
  import { onMount, onDestroy } from 'svelte';
  import { health } from '../lib/stores/health.js';
  import { timers, startTimerPolling } from '../lib/stores/timers.js';
  import { getStatus, getConversationDates, getConversationLog,
           endSession, restartService, refreshBriefings } from '../lib/api.js';
  import { config, loadConfig } from '../lib/stores/config.js';
  import { toast } from '../lib/stores/toast.js';
  import StatusBadge from '../lib/components/StatusBadge.svelte';
  import TimerCard from '../lib/components/TimerCard.svelte';

  let status = {};
  $: usage = status.usage || {};
  $: perf = status.performance || {};
  $: alerts = status.alerts || [];
  let recentTurns = [];
  let stopPolling;

  // Per-action busy state
  let busy = { endSession: false, restart: false, briefings: false };

  onMount(async () => {
    stopPolling = startTimerPolling();
    try { await loadConfig(); } catch { /* ignore */ }
    try { status = await getStatus(); } catch { /* ignore */ }
    await loadRecentConversations();
  });

  onDestroy(() => { if (stopPolling) stopPolling(); });

  async function loadRecentConversations() {
    try {
      const dates = await getConversationDates();
      if (dates.dates && dates.dates.length > 0) {
        const latest = dates.dates[0];
        const log = await getConversationLog(latest);
        recentTurns = (log.entries || [])
          .filter(e => e.type === 'turn')
          .slice(-10)
          .reverse();
      }
    } catch { /* ignore */ }
  }

  async function handleEndSession() {
    busy.endSession = true;
    try {
      const r = await endSession();
      toast.push(r.status === 'no_session' ? 'No active session' : 'Session ended', 'success');
    } catch (e) {
      toast.push('Failed to end session', 'error');
    }
    busy.endSession = false;
  }

  async function handleRestart() {
    busy.restart = true;
    try {
      await restartService();
      toast.push('Service restarting…', 'success');
    } catch (e) {
      toast.push('Restart failed', 'error');
    }
    busy.restart = false;
  }

  async function handleRefreshBriefings() {
    busy.briefings = true;
    try {
      await refreshBriefings();
      toast.push('Briefings refreshed', 'success');
    } catch (e) {
      toast.push('Refresh failed', 'error');
    }
    busy.briefings = false;
  }

  const btnBase = 'px-4 py-2 rounded text-sm font-medium transition-opacity disabled:opacity-40';
  const btnPrimary = btnBase + ' bg-accent text-bg hover:opacity-90';
  const btnSecondary = btnBase + ' bg-bg-input border border-border text-text-default hover:border-accent';
</script>

<div class="space-y-6">
  <div class="bg-bg-card border border-border rounded-lg p-6 flex items-center gap-6">
    <img src="/assets/Bender_Rodriguez.png" alt="Bender" class="w-20 h-20 object-contain" />
    <div>
      <h2 class="text-2xl font-bold text-accent">Good news, everyone!</h2>
      <p class="text-text-muted text-sm mt-1">
        {$health.status === 'online' ? "Bender is online and ready to insult humanity." : "Bender appears to be offline."}
      </p>
    </div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">Status</div>
      <div class="mt-1"><StatusBadge status={$health.status} label={$health.status === 'online' ? 'Online' : 'Offline'} /></div>
    </div>
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">AI Backend</div>
      <div class="text-lg font-semibold text-accent mt-1">{$config.ai_backend || '—'}</div>
    </div>
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">Sessions (7d)</div>
      <div class="text-lg font-semibold text-accent mt-1">{usage.sessions ?? '—'}</div>
    </div>
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">Errors (7d)</div>
      <div class="text-lg font-semibold {(status.health?.errors_7d || 0) > 0 ? 'text-error' : 'text-success'} mt-1">{status.health?.errors_7d ?? '—'}</div>
    </div>
  </div>

  <!-- Quick Actions -->
  <div class="bg-bg-card border border-border rounded-lg p-4">
    <h3 class="text-[11px] text-text-muted uppercase tracking-wider mb-3">Quick Actions</h3>
    <div class="flex flex-wrap gap-2">
      <button class={btnPrimary} disabled={busy.endSession} on:click={handleEndSession}>
        {busy.endSession ? 'Ending…' : 'End Session'}
      </button>
      <button class={btnSecondary} disabled={busy.restart} on:click={handleRestart}>
        {busy.restart ? 'Restarting…' : 'Restart Service'}
      </button>
      <button class={btnSecondary} disabled={busy.briefings} on:click={handleRefreshBriefings}>
        {busy.briefings ? 'Refreshing…' : 'Refresh Briefings'}
      </button>
    </div>
  </div>

  <!-- Performance -->
  {#if perf.response_total_ms}
    <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
      {#each [
        ['Avg Response', perf.response_total_ms, 'ms'],
        ['STT Record', perf.stt_record_ms, 'ms'],
        ['STT Transcribe', perf.stt_transcribe_ms, 'ms'],
        ['TTS Generate', perf.tts_generate_ms, 'ms'],
        ['AI API Call', perf.ai_api_call_ms, 'ms'],
        ['Audio Play', perf.audio_play_ms, 'ms'],
      ] as [label, value, unit]}
        {#if value != null}
          <div class="bg-bg-card border border-border rounded-lg p-3">
            <div class="text-[10px] text-text-muted uppercase">{label}</div>
            <div class="text-sm font-mono text-accent mt-1">{Math.round(value)}{unit}</div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <!-- Alerts -->
  {#if alerts.length > 0}
    <div>
      <h3 class="text-xs text-text-muted uppercase tracking-wider mb-2">Watchdog Alerts</h3>
      <div class="space-y-2">
        {#each alerts as alert}
          <div class="bg-bg-card border rounded-lg p-3 flex items-start gap-3
                      {alert.severity === 'error' ? 'border-error' : 'border-warning'}">
            <span class="text-sm {alert.severity === 'error' ? 'text-error' : 'text-warning'}">
              {alert.severity === 'error' ? '●' : '▲'}
            </span>
            <div>
              <div class="text-sm text-text-default">{alert.message}</div>
              <div class="text-xs text-text-muted mt-1">{alert.check}</div>
            </div>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if $timers.length > 0}
    <div>
      <h3 class="text-xs text-text-muted uppercase tracking-wider mb-2">Active Timers</h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        {#each $timers as timer (timer.id)}
          <TimerCard {timer} onUpdate={() => {}} />
        {/each}
      </div>
    </div>
  {/if}

  <div class="bg-bg-card border border-border rounded-lg p-4">
    <h3 class="text-[11px] text-text-muted uppercase tracking-wider mb-3">Recent Conversations</h3>
    {#if recentTurns.length === 0}
      <p class="text-text-muted text-sm flex items-center gap-2">
        <img src="/assets/bender-cigar.png" alt="" class="w-6 h-6 object-contain" />
        No conversations yet today.
      </p>
    {:else}
      <div class="space-y-1 font-mono text-sm">
        {#each recentTurns as turn}
          <div class="text-text-muted">
            <span class="opacity-60">{turn.ts?.split('T')[1]?.slice(0, 5) || ''}</span>
            — "{turn.user_text || ''}"
            → <span class="text-accent">{turn.method || ''}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
