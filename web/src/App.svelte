<script>
  import { onMount } from 'svelte';
  import { session } from './lib/stores/session.js';
  import { getServiceStatus } from './lib/api.js';
  import Login from './pages/Login.svelte';
  import Sidebar from './lib/components/Sidebar.svelte';
  import Puppet from './pages/Puppet.svelte';
  import Dashboard from './pages/Dashboard.svelte';
  import Config from './pages/Config.svelte';

  let checking = true;
  let currentPage = 'dashboard';

  onMount(async () => {
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
  <div class="min-h-screen flex items-center justify-center bg-bg">
    <img src="/assets/icons8-futurama-bender.svg" alt="Loading" class="w-16 h-16 animate-pulse" />
  </div>
{:else if !$session.authenticated}
  <Login />
{:else}
  <div class="min-h-screen flex bg-bg text-text-default font-sans">
    <Sidebar bind:currentPage />
    <main class="flex-1 p-6 overflow-y-auto">
      <div class="text-[11px] text-text-muted uppercase tracking-wider mb-4">
        {currentPage}
      </div>
      {#if currentPage === 'dashboard'}
        <Dashboard />
      {:else if currentPage === 'puppet'}
        <Puppet />
      {:else if currentPage === 'config'}
        <Config />
      {:else}
        <p class="text-text-muted">Page content coming next...</p>
      {/if}
    </main>
  </div>
{/if}
