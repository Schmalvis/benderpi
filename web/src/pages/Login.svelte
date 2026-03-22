<script>
  import { session } from '../lib/stores/session.js';
  import { getServiceStatus } from '../lib/api.js';

  let pin = '';
  let error = '';
  let loading = false;

  async function handleLogin() {
    if (!pin.trim()) return;
    loading = true;
    error = '';

    sessionStorage.setItem('benderpi_pin', pin);
    session.restore(pin);

    try {
      await getServiceStatus();
      session.login(pin);
    } catch (e) {
      error = 'Invalid PIN. Try again, meatbag.';
      session.logout();
    } finally {
      loading = false;
    }
  }

  function handleKeydown(e) {
    if (e.key === 'Enter') handleLogin();
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-bg">
  <div class="bg-bg-card border border-border rounded-lg shadow-lg p-8 w-full max-w-sm text-center">
    <img
      src="/assets/Futurama-Bender.webp"
      alt="Bender"
      class="w-32 h-32 mx-auto mb-6 object-contain"
    />
    <h1 class="text-accent text-2xl font-bold mb-2">BenderPi</h1>
    <p class="text-text-muted text-sm mb-6">Enter PIN to continue</p>

    <input
      type="password"
      inputmode="numeric"
      pattern="[0-9]*"
      maxlength="16"
      bind:value={pin}
      on:keydown={handleKeydown}
      placeholder="PIN"
      class="w-full bg-bg-input border border-border rounded px-4 py-3 text-text-default
             text-center text-lg tracking-widest focus:outline-none focus:border-accent
             transition-colors"
      disabled={loading}
    />

    {#if error}
      <p class="text-error text-sm mt-3">{error}</p>
    {/if}

    <button
      on:click={handleLogin}
      disabled={loading || !pin.trim()}
      class="mt-4 w-full bg-accent text-bg font-bold py-3 rounded
             hover:opacity-90 transition-opacity disabled:opacity-50"
    >
      {loading ? 'Authenticating...' : 'Bite my shiny metal app'}
    </button>
  </div>
</div>
