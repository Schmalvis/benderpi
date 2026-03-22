<script>
  import { onMount } from 'svelte';
  import { getClips, speak } from '../lib/api.js';
  import ClipButton from '../lib/components/ClipButton.svelte';

  let clips = [];
  let ttsText = '';
  let speaking = false;
  let refreshKey = 0;

  $: grouped = groupByCategory(clips);
  $: favourites = clips.filter(c => c.favourite);

  onMount(loadClips);

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
    } catch { /* ignore */ }
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
</script>

<div class="space-y-6">
  <div class="bg-bg-card border border-border rounded-lg p-4">
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
