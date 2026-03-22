<script>
  import { getVolume, setVolume } from '../api.js';
  import { onMount } from 'svelte';

  let volume = 80;
  let inflight = false;

  onMount(async () => {
    try {
      const data = await getVolume();
      volume = data.volume ?? 80;
    } catch { /* use default */ }
  });

  async function handleChange() {
    if (inflight) return;
    inflight = true;
    try {
      await setVolume(volume);
    } catch { /* ignore */ }
    inflight = false;
  }
</script>

<div class="text-xs text-text-muted mb-1">Volume</div>
<input
  type="range"
  min="0"
  max="100"
  bind:value={volume}
  on:change={handleChange}
  on:input={handleChange}
  class="w-full h-1 bg-bg-input rounded appearance-none cursor-pointer
         [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3
         [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-accent
         [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:shadow-glow"
/>
<div class="text-xs text-text-muted text-right">{volume}%</div>
