<script>
  import { onMount } from 'svelte';
  import { session } from './lib/stores/session.js';
  import { getServiceStatus } from './lib/api.js';
  import Login from './pages/Login.svelte';

  let checking = true;

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
  <div class="min-h-screen bg-bg text-text-default font-sans">
    <p class="p-4 text-accent">Authenticated! Shell coming next.</p>
  </div>
{/if}
