<script>
  import { onMount } from 'svelte';
  import { session } from './lib/stores/session.js';
  import { theme } from './lib/stores/theme.js';
  import { getServiceStatus } from './lib/api.js';
  import Login from './pages/Login.svelte';
  import Sidebar from './lib/components/Sidebar.svelte';
  import Puppet from './pages/Puppet.svelte';
  import Dashboard from './pages/Dashboard.svelte';
  import Config from './pages/Config.svelte';
  import Remote from './pages/Remote.svelte';
  import Logs from './pages/Logs.svelte';
  import Toast from './lib/components/Toast.svelte';

  let checking = true;
  let currentPage = 'dashboard';

  onMount(async () => {
    theme.init();
    const { pin } = $session;
    if (pin) {
      try {
        session.restore(pin);
        await getServiceStatus();
        session.login(pin);
      } catch {
        session.logout();
      }
    }
    checking = false;
  });
</script>

{#if checking}
  <div class="min-h-screen flex flex-col items-center justify-center" style="background: var(--bg);">
    <img src="/assets/icons8-futurama-bender.svg" alt="Loading" class="w-12 h-12 animate-avatar-glow mb-4" />
    <div class="font-display text-[10px] tracking-widest animate-pulse" style="color: var(--text-muted); letter-spacing: 0.2em;">INITIALISING...</div>
  </div>
{:else if !$session.authenticated}
  <Login />
{:else}
  <div class="min-h-screen flex flex-col md:flex-row bg-bg text-text-default font-sans">
    <Sidebar bind:currentPage />
    <main class="flex-1 p-4 md:p-6 overflow-y-auto pb-20 md:pb-6">
      <div class="font-display text-[9px] uppercase mb-5" style="color: var(--text-muted); letter-spacing: 0.2em;">
        ▸ {currentPage}
      </div>
      {#if currentPage === 'dashboard'}
        <Dashboard />
      {:else if currentPage === 'puppet'}
        <Puppet />
      {:else if currentPage === 'config'}
        <Config />
      {:else if currentPage === 'remote'}
        <Remote />
      {:else if currentPage === 'logs'}
        <Logs />
      {:else}
        <p class="text-text-muted">Page content coming next...</p>
      {/if}
    </main>
  </div>
  <Toast />
{/if}
