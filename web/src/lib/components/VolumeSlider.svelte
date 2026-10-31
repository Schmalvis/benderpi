<script>
  import { getVolume, setVolume } from '../api.js';
  import { onMount } from 'svelte';
  import { toast } from '../stores/toast.js';

  // null until Bender reports the real level. This used to start at 80 and
  // stay there, because the page read `data.volume` and the route sends
  // `level`, so the slider showed a number nothing had measured.
  let volume = null;
  let sending = false;
  let wanted = null;

  onMount(async () => {
    try {
      const data = await getVolume();
      volume = data.level ?? null;
    } catch { /* the slider stays disabled: there is no level to show */ }
  });

  // Dragging fires faster than amixer answers. Keep only the newest value and
  // send it when the request in flight returns, so the slider cannot end on a
  // level that was never sent.
  async function handleChange() {
    wanted = volume;
    if (sending) return;
    sending = true;
    try {
      while (wanted !== null) {
        const level = wanted;
        wanted = null;
        await setVolume(level);
      }
    } catch (e) {
      wanted = null;
      toast.push(`Volume not set: ${e.message}`, 'error');
    }
    sending = false;
  }
</script>

<div class="text-xs text-text-muted mb-1">Volume</div>
<input
  type="range"
  min="0"
  max="100"
  disabled={volume === null}
  bind:value={volume}
  on:change={handleChange}
  on:input={handleChange}
  class="w-full h-1 bg-bg-input rounded appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed
         [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3
         [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-accent
         [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:shadow-glow"
/>
<div class="text-xs text-text-muted text-right">{volume === null ? '?' : `${volume}%`}</div>
