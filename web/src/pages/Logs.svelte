<script>
  import { onMount, tick } from 'svelte';
  import { getConversationDates, getConversationLog, getSystemLog, getMetrics, downloadLog } from '../lib/api.js';

  let activeTab = 'conversations';
  let dates = [];
  let selectedDate = '';
  let entries = [];
  let systemLog = '';
  let systemLogLines = 100;
  let metrics = {};
  let loading = false;
  let systemLogEl;

  onMount(loadDates);

  async function loadDates() {
    try {
      const data = await getConversationDates();
      dates = data.dates || [];
      if (dates.length > 0) {
        selectedDate = dates[0];
        await loadLog(selectedDate);
      }
    } catch { /* ignore */ }
  }

  async function loadLog(date) {
    loading = true;
    try {
      const data = await getConversationLog(date);
      entries = data.entries || [];
    } catch { entries = []; }
    loading = false;
  }

  async function loadSystemLog() {
    loading = true;
    try {
      const data = await getSystemLog(systemLogLines);
      systemLog = data.log || '';
    } catch { systemLog = 'Error loading log'; }
    loading = false;
    await tick();
    if (systemLogEl) systemLogEl.scrollTop = systemLogEl.scrollHeight;
  }

  async function loadMetrics() {
    loading = true;
    try {
      metrics = await getMetrics();
    } catch { metrics = {}; }
    loading = false;
  }

  function handleTabChange(tab) {
    activeTab = tab;
    if (tab === 'system' && !systemLog) loadSystemLog();
    if (tab === 'metrics' && !metrics.entries) loadMetrics();
  }

  function groupBySessions(entryList) {
    const sessions = [];
    let current = null;
    for (const e of entryList) {
      if (e.type === 'session_start') {
        current = { start: e, turns: [], end: null };
        sessions.push(current);
      } else if (e.type === 'turn' && current) {
        current.turns.push(e);
      } else if (e.type === 'session_end' && current) {
        current.end = e;
      }
    }
    return sessions.reverse();
  }

  function formatTime(ts) {
    if (!ts) return '';
    try {
      return new Date(ts).toLocaleTimeString();
    } catch { return ts; }
  }

  function truncate(text, len = 120) {
    if (!text) return '';
    return text.length > len ? text.slice(0, len) + '…' : text;
  }

  function methodBadgeClass(method) {
    const map = {
      real_clip: 'bg-green-900 text-green-300',
      pre_gen_tts: 'bg-blue-900 text-blue-300',
      promoted_tts: 'bg-purple-900 text-purple-300',
      handler_weather: 'bg-yellow-900 text-yellow-300',
      handler_news: 'bg-yellow-900 text-yellow-300',
      handler_ha: 'bg-orange-900 text-orange-300',
      ai_fallback: 'bg-red-900 text-red-300',
      error_fallback: 'bg-gray-700 text-gray-300',
    };
    return map[method] || 'bg-gray-700 text-gray-300';
  }

  let expandedSessions = new Set();

  function toggleSession(i) {
    if (expandedSessions.has(i)) {
      expandedSessions.delete(i);
    } else {
      expandedSessions.add(i);
    }
    expandedSessions = expandedSessions;
  }

  $: sessions = groupBySessions(entries);

  // Log files available for download
  const logFiles = [
    { label: 'System Log (bender.log)', filename: 'bender.log' },
    { label: 'Metrics (metrics.jsonl)', filename: 'metrics.jsonl' },
  ];
</script>

<div class="max-w-5xl mx-auto">
  <h1 class="text-xl font-semibold text-text-default mb-4">Logs</h1>

  <!-- Sub-view tabs -->
  <div class="flex gap-1 mb-6">
    {#each [['conversations', 'Conversations'], ['system', 'System Log'], ['metrics', 'Metrics']] as [id, label]}
      <button
        class="px-4 py-2 rounded text-sm transition-colors
               {activeTab === id ? 'bg-bg-card text-accent border border-border' : 'text-text-muted hover:text-text-default'}"
        on:click={() => handleTabChange(id)}
      >{label}</button>
    {/each}
  </div>

  <!-- CONVERSATIONS TAB -->
  {#if activeTab === 'conversations'}
    <div class="flex gap-4">
      <!-- Date list -->
      <div class="w-44 shrink-0">
        <div class="text-xs text-text-muted uppercase tracking-wider mb-2">Dates</div>
        {#if dates.length === 0}
          <p class="text-text-muted text-sm">No logs found.</p>
        {:else}
          <ul class="space-y-1">
            {#each dates as date}
              <li>
                <button
                  class="w-full text-left px-3 py-1.5 rounded text-sm transition-colors
                         {selectedDate === date ? 'bg-bg-card text-accent border border-border' : 'text-text-muted hover:text-text-default hover:bg-bg-card'}"
                  on:click={() => { selectedDate = date; loadLog(date); }}
                >{date}</button>
              </li>
            {/each}
          </ul>
        {/if}

        <!-- Downloads -->
        {#if dates.length > 0}
          <div class="mt-6">
            <div class="text-xs text-text-muted uppercase tracking-wider mb-2">Downloads</div>
            <ul class="space-y-1">
              {#each logFiles as f}
                <li>
                  <a
                    href={downloadLog(f.filename)}
                    download={f.filename}
                    class="text-xs text-accent hover:underline block truncate"
                  >{f.label}</a>
                </li>
              {/each}
              {#if selectedDate}
                <li>
                  <a
                    href={downloadLog(`${selectedDate}.jsonl`)}
                    download="{selectedDate}.jsonl"
                    class="text-xs text-accent hover:underline block truncate"
                  >{selectedDate}.jsonl</a>
                </li>
              {/if}
            </ul>
          </div>
        {/if}
      </div>

      <!-- Session list -->
      <div class="flex-1 min-w-0">
        {#if loading}
          <p class="text-text-muted text-sm">Loading…</p>
        {:else if sessions.length === 0}
          <p class="text-text-muted text-sm">No sessions for this date.</p>
        {:else}
          <div class="space-y-2">
            {#each sessions as session, i}
              <div class="bg-bg-card border border-border rounded">
                <!-- Session header -->
                <button
                  class="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-bg transition-colors rounded"
                  on:click={() => toggleSession(i)}
                >
                  <div class="flex items-center gap-3">
                    <span class="text-sm font-medium text-text-default">
                      Session {formatTime(session.start?.timestamp)}
                    </span>
                    <span class="text-xs text-text-muted">
                      {session.turns.length} turn{session.turns.length !== 1 ? 's' : ''}
                    </span>
                    {#if session.end}
                      <span class="text-xs text-text-muted">
                        ended {formatTime(session.end.timestamp)}
                      </span>
                    {/if}
                  </div>
                  <span class="text-text-muted text-xs">{expandedSessions.has(i) ? '▲' : '▼'}</span>
                </button>

                <!-- Turns -->
                {#if expandedSessions.has(i)}
                  <div class="border-t border-border divide-y divide-border">
                    {#each session.turns as turn}
                      <div class="px-4 py-3">
                        <div class="flex items-start gap-3">
                          <span class="text-xs text-text-muted mt-0.5 shrink-0 w-16">{formatTime(turn.timestamp)}</span>
                          <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2 mb-1">
                              <span class="text-xs px-1.5 py-0.5 rounded font-mono {methodBadgeClass(turn.method)}">{turn.method || 'unknown'}</span>
                            </div>
                            {#if turn.user_text}
                              <p class="text-sm text-text-default mb-1">
                                <span class="text-text-muted">You:</span> {turn.user_text}
                              </p>
                            {/if}
                            {#if turn.response_text}
                              <p class="text-sm text-text-muted">
                                <span>Bender:</span> {truncate(turn.response_text)}
                              </p>
                            {/if}
                          </div>
                        </div>
                      </div>
                    {/each}
                    {#if session.turns.length === 0}
                      <div class="px-4 py-3 text-sm text-text-muted">No turns recorded.</div>
                    {/if}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

  <!-- SYSTEM LOG TAB -->
  {:else if activeTab === 'system'}
    <div class="flex items-center gap-3 mb-4">
      <label for="log-lines" class="text-sm text-text-muted">Lines:</label>
      <select
        id="log-lines"
        bind:value={systemLogLines}
        class="bg-bg-card border border-border text-text-default text-sm rounded px-2 py-1"
        on:change={loadSystemLog}
      >
        <option value={50}>50</option>
        <option value={100}>100</option>
        <option value={200}>200</option>
        <option value={500}>500</option>
      </select>
      <button
        class="px-3 py-1 text-sm bg-bg-card border border-border text-text-default rounded hover:border-accent transition-colors"
        on:click={loadSystemLog}
      >Refresh</button>
    </div>

    {#if loading}
      <p class="text-text-muted text-sm">Loading…</p>
    {:else}
      <pre
        bind:this={systemLogEl}
        class="bg-bg-card border border-border rounded p-4 text-xs font-mono text-text-default overflow-auto h-[60vh] whitespace-pre-wrap break-all"
      >{systemLog || 'No log data.'}</pre>
    {/if}

  <!-- METRICS TAB -->
  {:else if activeTab === 'metrics'}
    {#if loading}
      <p class="text-text-muted text-sm">Loading…</p>
    {:else if !metrics || Object.keys(metrics).length === 0}
      <p class="text-text-muted text-sm">No metrics available.</p>
    {:else}
      <div class="space-y-4">
        {#if metrics.entries && Array.isArray(metrics.entries)}
          <div class="bg-bg-card border border-border rounded overflow-hidden">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border">
                  <th class="text-left px-4 py-2 text-text-muted font-medium">Time</th>
                  <th class="text-left px-4 py-2 text-text-muted font-medium">Event</th>
                  <th class="text-left px-4 py-2 text-text-muted font-medium">Value</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-border">
                {#each metrics.entries as entry}
                  <tr class="hover:bg-bg transition-colors">
                    <td class="px-4 py-2 text-text-muted text-xs font-mono">{formatTime(entry.timestamp || entry.ts)}</td>
                    <td class="px-4 py-2 text-text-default">{entry.event || entry.name || entry.metric || JSON.stringify(entry).slice(0, 60)}</td>
                    <td class="px-4 py-2 text-text-muted text-xs font-mono">{entry.value ?? entry.duration_ms ?? ''}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {:else}
          <!-- Fallback: render as key-value pairs -->
          <div class="bg-bg-card border border-border rounded p-4">
            <dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              {#each Object.entries(metrics) as [key, value]}
                <dt class="text-text-muted font-medium truncate">{key}</dt>
                <dd class="text-text-default font-mono text-xs">{typeof value === 'object' ? JSON.stringify(value) : value}</dd>
              {/each}
            </dl>
          </div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
