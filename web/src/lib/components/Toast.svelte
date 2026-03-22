<script>
  import { fly } from 'svelte/transition';
  import { toast } from '../stores/toast.js';
</script>

<div class="fixed bottom-4 right-4 flex flex-col gap-2 z-50 pointer-events-none">
  {#each $toast as t (t.id)}
    <div
      in:fly={{ y: 16, duration: 150 }}
      out:fly={{ y: 16, duration: 150 }}
      class="px-4 py-2 rounded shadow-lg text-sm font-medium pointer-events-auto select-none
             {t.type === 'error' ? 'bg-red-600 text-white' : 'bg-green-700 text-white'}"
    >
      {t.message}
    </div>
  {/each}
</div>
