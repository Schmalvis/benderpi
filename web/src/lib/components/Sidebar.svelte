<script>
  import { health } from '../stores/health.js';
  import { session } from '../stores/session.js';
  import { endSession } from '../api.js';
  import VolumeSlider from './VolumeSlider.svelte';

  export let currentPage = 'dashboard';

  let menuOpen = false;

  const pages = [
    { id: 'dashboard', label: 'Dashboard', icon: '📊' },
    { id: 'puppet', label: 'Puppet', icon: '🎭' },
    { id: 'config', label: 'Config', icon: '⚙️' },
    { id: 'logs', label: 'Logs', icon: '📋' },
    { id: 'remote', label: 'Remote', icon: '🎤' },
  ];

  async function handleEndSession() {
    try { await endSession(); } catch { /* ignore */ }
    menuOpen = false;
  }
</script>

<!-- Desktop sidebar — hidden on mobile -->
<aside class="hidden md:flex w-56 bg-bg-sidebar border-r border-border flex-col p-4 shrink-0">
  <div class="flex items-center gap-3 mb-6 pb-4 border-b border-border">
    <img
      src="/assets/icons8-futurama-bender.svg"
      alt="Bender"
      class="w-10 h-10 rounded-full border-2 border-accent animate-avatar-glow"
    />
    <div>
      <div class="text-xl font-extrabold text-accent" style="text-shadow: 0 0 12px var(--glow)">
        BenderPi
      </div>
      <div class="text-[10px] text-text-muted uppercase tracking-widest">Voice Assistant</div>
    </div>
  </div>

  <nav class="flex flex-col gap-0.5">
    {#each pages as page}
      <button
        class="flex items-center gap-2 px-3 py-2.5 rounded text-sm transition-all text-left w-full
               {currentPage === page.id
                 ? 'bg-bg-card text-accent shadow-[inset_3px_0_0_var(--accent)]'
                 : 'text-text-muted hover:bg-bg-card hover:text-text-default'}"
        on:click={() => currentPage = page.id}
      >
        <span>{page.icon}</span>
        <span>{page.label}</span>
      </button>
    {/each}
  </nav>

  <div class="mt-auto pt-4 border-t border-border">
    <div class="text-[10px] text-text-muted uppercase tracking-widest mb-2">Quick Controls</div>
    <VolumeSlider />
    <div class="flex gap-1.5 mt-2">
      <button
        on:click={handleEndSession}
        class="flex-1 bg-bg-input border border-border rounded px-2 py-1.5
               text-[10px] text-text-muted hover:text-accent hover:border-accent transition-colors"
      >
        End Session
      </button>
      <button
        on:click={() => session.logout()}
        class="flex-1 bg-bg-input border border-border rounded px-2 py-1.5
               text-[10px] text-text-muted hover:text-error hover:border-error transition-colors"
      >
        Logout
      </button>
    </div>
  </div>

  <div class="mt-3 text-[10px] text-text-muted flex items-center gap-1.5">
    <span class="inline-block w-2 h-2 rounded-full {$health.status === 'online' ? 'bg-success animate-status-pulse' : 'bg-error'}"></span>
    {$health.status === 'online' ? 'Connected' : 'Offline'}
  </div>
</aside>

<!-- Mobile bottom tab bar — hidden on desktop -->
<nav class="fixed bottom-0 left-0 right-0 bg-bg-sidebar border-t border-border flex justify-around py-2 z-50 md:hidden">
  {#each pages as page}
    <button
      class="flex flex-col items-center justify-center w-10 h-10 rounded text-xl transition-all
             {currentPage === page.id ? 'text-accent' : 'text-text-muted'}"
      on:click={() => { currentPage = page.id; menuOpen = false; }}
      title={page.label}
    >
      {page.icon}
    </button>
  {/each}
  <!-- Menu toggle -->
  <button
    class="flex flex-col items-center justify-center w-10 h-10 rounded text-xl transition-all
           {menuOpen ? 'text-accent' : 'text-text-muted'}"
    on:click={() => menuOpen = !menuOpen}
    title="Menu"
  >
    ☰
  </button>
</nav>

<!-- Mobile slide-up menu panel -->
{#if menuOpen}
  <!-- Backdrop -->
  <div
    class="fixed inset-0 z-40 md:hidden"
    on:click={() => menuOpen = false}
    role="presentation"
  ></div>
  <!-- Panel -->
  <div class="fixed bottom-14 left-0 right-0 bg-bg-sidebar border-t border-border p-4 z-50 md:hidden rounded-t-xl shadow-lg">
    <div class="text-[10px] text-text-muted uppercase tracking-widest mb-3">Quick Controls</div>
    <VolumeSlider />
    <div class="flex gap-2 mt-3">
      <button
        on:click={handleEndSession}
        class="flex-1 bg-bg-input border border-border rounded px-3 py-2
               text-xs text-text-muted hover:text-accent hover:border-accent transition-colors"
      >
        End Session
      </button>
      <button
        on:click={() => { session.logout(); menuOpen = false; }}
        class="flex-1 bg-bg-input border border-border rounded px-3 py-2
               text-xs text-text-muted hover:text-error hover:border-error transition-colors"
      >
        Logout
      </button>
    </div>
    <div class="mt-3 text-[10px] text-text-muted flex items-center gap-1.5">
      <span class="inline-block w-2 h-2 rounded-full {$health.status === 'online' ? 'bg-success animate-status-pulse' : 'bg-error'}"></span>
      {$health.status === 'online' ? 'Connected' : 'Offline'}
    </div>
  </div>
{/if}
