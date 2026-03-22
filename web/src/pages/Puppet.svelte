<script>
  import { onMount, onDestroy } from 'svelte';
  import { get } from 'svelte/store';
  import { getClips, speak } from '../lib/api.js';
  import { session } from '../lib/stores/session.js';
  import { toast } from '../lib/stores/toast.js';
  import ClipButton from '../lib/components/ClipButton.svelte';

  let clips = [];
  let ttsText = '';
  let speaking = false;
  let refreshKey = 0;

  // Mic streaming state
  let micActive = false;
  let micWs = null;
  let audioCtx = null;
  let nextPlayTime = 0;

  $: grouped = groupByCategory(clips);
  $: favourites = clips.filter(c => c.favourite);

  onMount(loadClips);
  onDestroy(stopMic);

  async function loadClips() {
    try {
      const data = await getClips();
      clips = data.clips || [];
    } catch { /* ignore */ }
  }

  function groupByCategory(clipList) {
    const groups = {};
    for (const clip of clipList) {
      const cat = clip.category || 'clips';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(clip);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }

  async function handleSpeak() {
    if (!ttsText.trim() || speaking) return;
    speaking = true;
    try {
      await speak(ttsText);
      ttsText = '';
      toast.push('Speaking…', 'success');
    } catch {
      toast.push('Speak failed', 'error');
    }
    speaking = false;
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSpeak();
    }
  }

  function handleFavToggle() {
    refreshKey++;
    clips = [...clips];
  }

  // ── Mic streaming ──────────────────────────────────────────────

  function scheduleChunk(arrayBuffer) {
    if (!audioCtx) return;
    const SAMPLE_RATE = 16000;
    const view = new Int16Array(arrayBuffer);
    const audioBuffer = audioCtx.createBuffer(1, view.length, SAMPLE_RATE);
    const channel = audioBuffer.getChannelData(0);
    for (let i = 0; i < view.length; i++) {
      channel[i] = view[i] / 32768.0;
    }
    const source = audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioCtx.destination);
    const startAt = Math.max(audioCtx.currentTime + 0.1, nextPlayTime);
    source.start(startAt);
    nextPlayTime = startAt + audioBuffer.duration;
  }

  function startMic() {
    const { pin } = get(session);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/puppet/mic?pin=${encodeURIComponent(pin)}`);
    ws.binaryType = 'arraybuffer';

    audioCtx = new AudioContext({ sampleRate: 16000 });
    nextPlayTime = 0;

    ws.onopen = () => {
      micActive = true;
      toast.push('Hearing room…', 'success');
    };

    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        scheduleChunk(e.data);
      }
    };

    ws.onerror = () => {
      toast.push('Mic stream error', 'error');
      cleanupMic();
    };

    ws.onclose = () => {
      cleanupMic();
    };

    micWs = ws;
  }

  function stopMic() {
    if (micWs) {
      micWs.onclose = null;
      micWs.close();
      micWs = null;
    }
    cleanupMic();
  }

  function cleanupMic() {
    micActive = false;
    nextPlayTime = 0;
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
    }
  }

  function toggleMic() {
    if (micActive) {
      stopMic();
    } else {
      startMic();
    }
  }
</script>

<div class="space-y-6">
  <!-- TTS input + Hear Room -->
  <div class="bg-bg-card border border-border rounded-lg p-4 space-y-3">
    <div class="flex gap-3">
      <input
        type="text"
        bind:value={ttsText}
        on:keydown={handleKeydown}
        placeholder="Type something for Bender to say..."
        class="flex-1 bg-bg-input border border-border rounded px-4 py-2 text-text-default
               focus:outline-none focus:border-accent transition-colors"
        disabled={speaking}
      />
      <button
        on:click={handleSpeak}
        disabled={speaking || !ttsText.trim()}
        class="bg-accent text-bg font-bold px-6 py-2 rounded
               hover:opacity-90 transition-opacity disabled:opacity-50"
      >
        {speaking ? 'Speaking...' : 'Speak'}
      </button>
    </div>

    <!-- Ambient mic feed -->
    <div class="flex items-center gap-3 pt-1 border-t border-border">
      <button
        on:click={toggleMic}
        class="flex items-center gap-2 px-4 py-1.5 rounded text-sm font-medium transition-all
               {micActive
                 ? 'bg-error text-white animate-pulse'
                 : 'bg-bg-input border border-border text-text-muted hover:border-accent hover:text-text-default'}"
      >
        <span>{micActive ? '●' : '○'}</span>
        {micActive ? 'Stop Hearing Room' : 'Hear Room'}
      </button>
      {#if micActive}
        <span class="text-xs text-text-muted">Streaming ambient audio from Bender's mic</span>
      {/if}
    </div>
  </div>

  {#if favourites.length > 0}
    <div>
      <h3 class="text-xs text-text-muted uppercase tracking-wider mb-2">Favourites</h3>
      <div class="flex gap-2 overflow-x-auto pb-2">
        {#each favourites as clip (clip.path)}
          <div class="shrink-0 w-48">
            <ClipButton {clip} onFavouriteToggle={handleFavToggle} />
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#each grouped as [category, categoryClips] (category)}
    <details open class="group">
      <summary class="cursor-pointer text-xs text-text-muted uppercase tracking-wider mb-2
                      flex items-center gap-2 select-none">
        <span class="transition-transform group-open:rotate-90">▶</span>
        {category}
        <span class="bg-bg-input px-2 py-0.5 rounded text-[10px]">{categoryClips.length}</span>
      </summary>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2 mt-2">
        {#each categoryClips as clip (clip.path)}
          <ClipButton {clip} onFavouriteToggle={handleFavToggle} />
        {/each}
      </div>
    </details>
  {/each}
</div>
