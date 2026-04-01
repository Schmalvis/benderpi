<script>
  import { health } from '../stores/health.js';
  import { theme } from '../stores/theme.js';
  import { session } from '../stores/session.js';
  import { endSession } from '../api.js';
  import VolumeSlider from './VolumeSlider.svelte';

  export let currentPage = 'dashboard';

  let menuOpen = false;

  const pages = [
    { id: 'dashboard', label: 'Dashboard', icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>` },
    { id: 'puppet', label: 'Puppet', icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="5"/><path d="M3 21v-2a7 7 0 0 1 7-7h4a7 7 0 0 1 7 7v2"/></svg>` },
    { id: 'config', label: 'Config', icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2"/></svg>` },
    { id: 'logs', label: 'Logs', icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>` },
    { id: 'remote', label: 'Remote', icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>` },
  ];

  async function handleEndSession() {
    try { await endSession(); } catch { /* ignore */ }
    menuOpen = false;
  }
</script>

<!-- Desktop sidebar -->
<aside class="hidden md:flex w-52 bg-bg-sidebar border-r border-border flex-col p-4 shrink-0" style="border-color: var(--border);">
  <!-- Brand -->
  <div class="flex items-center gap-3 mb-8 pb-4" style="border-bottom: 1px solid var(--border);">
    <div class="relative">
      <img
        src="/assets/icons8-futurama-bender.svg"
        alt="Bender"
        class="w-9 h-9 rounded animate-avatar-glow"
        style="border: 1px solid var(--accent); padding: 1px;"
      />
    </div>
    <div>
      <div class="font-display text-xs font-700 tracking-widest" style="color: var(--accent); letter-spacing: 0.15em;">BENDERPI</div>
      <div class="text-[9px] tracking-widest" style="color: var(--text-muted); letter-spacing: 0.12em;">CONTROL SYSTEM</div>
    </div>
  </div>

  <!-- Nav -->
  <nav class="flex-1 space-y-0.5">
    {#each pages as page}
      <button
        class="flex items-center gap-3 px-3 py-2.5 w-full rounded text-left transition-all group text-sm font-medium tracking-wide"
        style="
          background: {currentPage === page.id ? 'rgba(212,130,10,0.1)' : 'transparent'};
          color: {currentPage === page.id ? 'var(--accent)' : 'var(--text-muted)'};
          border-left: 2px solid {currentPage === page.id ? 'var(--accent)' : 'transparent'};
          padding-left: {currentPage === page.id ? '10px' : '12px'};
        "
        on:click={() => currentPage = page.id}
      >
        <span class="w-4 h-4 shrink-0" style="opacity: {currentPage === page.id ? '1' : '0.6'};">
          {@html page.icon}
        </span>
        <span class="font-display text-[11px] tracking-widest uppercase">{page.label}</span>
      </button>
    {/each}
  </nav>

  <!-- Quick Controls -->
  <div class="mt-auto pt-4" style="border-top: 1px solid var(--border);">
    <div class="font-display text-[9px] tracking-widest mb-3" style="color: var(--text-muted); letter-spacing: 0.15em;">QUICK CONTROLS</div>
    <VolumeSlider />
    <div class="flex gap-1.5 mt-3">
      <button
        on:click={handleEndSession}
        class="flex-1 rounded px-2 py-1.5 text-[10px] tracking-wide transition-all hover:border-accent"
        style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-muted); font-family: var(--font-display);"
        on:mouseenter={e => { e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.borderColor = 'var(--accent)'; }}
        on:mouseleave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'var(--border)'; }}
      >
        END
      </button>
      <button
        on:click={() => session.logout()}
        class="flex-1 rounded px-2 py-1.5 text-[10px] tracking-wide transition-all"
        style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-muted); font-family: var(--font-display);"
        on:mouseenter={e => { e.currentTarget.style.color = 'var(--error)'; e.currentTarget.style.borderColor = 'var(--error)'; }}
        on:mouseleave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'var(--border)'; }}
      >
        LOGOUT
      </button>
    </div>

    <!-- Theme toggle -->
    <button
      on:click={theme.toggle}
      class="w-full rounded px-2 py-1.5 text-[10px] tracking-wide transition-all mt-1.5"
      style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-muted); font-family: var(--font-display); letter-spacing: 0.12em;"
      on:mouseenter={e => { e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.borderColor = 'var(--accent)'; }}
      on:mouseleave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'var(--border)'; }}
    >
      {$theme === 'dark' ? '☀ LIGHT MODE' : '☾ DARK MODE'}
    </button>

    <div class="mt-3 flex items-center gap-2 text-[10px]" style="color: var(--text-muted);">
      <span class="inline-block w-1.5 h-1.5 rounded-full {$health.status === 'online' ? 'animate-status-pulse' : ''}"
        style="background: {$health.status === 'online' ? 'var(--success)' : 'var(--error)'};"></span>
      <span class="font-display tracking-widest text-[9px]" style="letter-spacing: 0.12em;">
        {$health.status === 'online' ? 'CONNECTED' : 'OFFLINE'}
      </span>
    </div>
  </div>
</aside>

<!-- Mobile bottom tab bar -->
<nav class="fixed bottom-0 left-0 right-0 flex justify-around py-2 z-50 md:hidden"
  style="background: var(--bg-sidebar); border-top: 1px solid var(--border);">
  {#each pages as page}
    <button
      class="flex flex-col items-center justify-center w-10 h-10 rounded transition-all"
      style="color: {currentPage === page.id ? 'var(--accent)' : 'var(--text-muted)'};"
      on:click={() => { currentPage = page.id; menuOpen = false; }}
      title={page.label}
    >
      <span class="w-5 h-5">{@html page.icon}</span>
    </button>
  {/each}
  <button
    class="flex flex-col items-center justify-center w-10 h-10 rounded transition-all"
    style="color: {menuOpen ? 'var(--accent)' : 'var(--text-muted)'};"
    on:click={() => menuOpen = !menuOpen}
    title="Menu"
  >
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="w-5 h-5"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
  </button>
</nav>

<!-- Mobile slide-up menu -->
{#if menuOpen}
  <div class="fixed inset-0 z-40 md:hidden" on:click={() => menuOpen = false} role="presentation"></div>
  <div class="fixed bottom-14 left-0 right-0 p-4 z-50 md:hidden rounded-t-lg shadow-lg"
    style="background: var(--bg-sidebar); border-top: 1px solid var(--border);">
    <div class="font-display text-[9px] tracking-widest mb-3" style="color: var(--text-muted); letter-spacing: 0.15em;">QUICK CONTROLS</div>
    <VolumeSlider />
    <div class="flex gap-2 mt-3">
      <button on:click={handleEndSession}
        class="flex-1 rounded py-2 text-xs font-display tracking-widest transition-all"
        style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-muted);">
        END SESSION
      </button>
      <button on:click={() => { session.logout(); menuOpen = false; }}
        class="flex-1 rounded py-2 text-xs font-display tracking-widest transition-all"
        style="background: var(--bg-input); border: 1px solid var(--error); color: var(--error);">
        LOGOUT
      </button>
    </div>
      <button
        on:click={() => { theme.toggle(); }}
        class="w-full rounded py-2 text-xs font-display tracking-widest transition-all mt-1"
        style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-muted);">
        {$theme === 'dark' ? '☀ LIGHT MODE' : '☾ DARK MODE'}
      </button>
    <div class="mt-3 flex items-center gap-2 text-[10px]" style="color: var(--text-muted);">
      <span class="inline-block w-1.5 h-1.5 rounded-full {$health.status === 'online' ? 'animate-status-pulse' : ''}"
        style="background: {$health.status === 'online' ? 'var(--success)' : 'var(--error)'};"></span>
      <span class="font-display tracking-widest text-[9px]">{$health.status === 'online' ? 'CONNECTED' : 'OFFLINE'}</span>
    </div>
  </div>
{/if}
