<script>
  import { playClip, setFavourite } from '../api.js';

  export let clip;
  export let onFavouriteToggle = () => {};

  let playing = false;

  async function handlePlay() {
    playing = true;
    try {
      await playClip(clip.path);
    } catch { /* ignore */ }
    playing = false;
  }

  async function handleStar(e) {
    e.stopPropagation();
    const newState = !clip.favourite;
    try {
      await setFavourite(clip.path, newState);
      clip.favourite = newState;
      onFavouriteToggle();
    } catch { /* ignore */ }
  }
</script>

<button
  on:click={handlePlay}
  class="relative bg-bg-card border border-border rounded px-3 py-2 text-left
         hover:border-accent hover:shadow-glow transition-all group w-full
         {playing ? 'border-accent shadow-glow' : ''}"
  title={clip.label}
>
  <div class="flex items-center gap-2">
    <span class="text-accent text-xs opacity-60 group-hover:opacity-100">▶</span>
    <span class="text-sm text-text-default truncate flex-1">{clip.label}</span>
    <button
      on:click={handleStar}
      class="text-sm hover:scale-125 transition-transform"
      title={clip.favourite ? 'Remove favourite' : 'Add favourite'}
    >
      {clip.favourite ? '★' : '☆'}
    </button>
  </div>
</button>
