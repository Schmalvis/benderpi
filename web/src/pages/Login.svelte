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
      error = 'INVALID PIN. TRY AGAIN, MEATBAG.';
      session.logout();
    } finally {
      loading = false;
    }
  }

  function handleKeydown(e) {
    if (e.key === 'Enter') handleLogin();
  }
</script>

<div class="min-h-screen flex items-center justify-center relative overflow-hidden"
  style="background: var(--bg);">

  <!-- Background grid pattern -->
  <div class="absolute inset-0 opacity-30" style="
    background-image:
      linear-gradient(rgba(212,130,10,0.05) 1px, transparent 1px),
      linear-gradient(90deg, rgba(212,130,10,0.05) 1px, transparent 1px);
    background-size: 40px 40px;
  "></div>

  <!-- Vignette -->
  <div class="absolute inset-0 pointer-events-none" style="
    background: radial-gradient(ellipse at center, transparent 40%, rgba(12,10,8,0.85) 100%);
  "></div>

  <div class="relative z-10 w-full max-w-sm px-4 animate-scan-in">
    <!-- Corner brackets -->
    <div class="relative p-8 rounded" style="
      background: var(--bg-card);
      border: 1px solid var(--border);
      box-shadow: 0 0 40px rgba(212,130,10,0.08), 0 20px 60px rgba(0,0,0,0.8);
    ">
      <!-- Corner decorations -->
      <div class="absolute top-0 left-0 w-4 h-4" style="border-top: 2px solid var(--accent); border-left: 2px solid var(--accent);"></div>
      <div class="absolute top-0 right-0 w-4 h-4" style="border-top: 2px solid var(--accent); border-right: 2px solid var(--accent);"></div>
      <div class="absolute bottom-0 left-0 w-4 h-4" style="border-bottom: 2px solid var(--accent); border-left: 2px solid var(--accent);"></div>
      <div class="absolute bottom-0 right-0 w-4 h-4" style="border-bottom: 2px solid var(--accent); border-right: 2px solid var(--accent);"></div>

      <!-- Bender face -->
      <div class="flex justify-center mb-6">
        <div class="relative">
          <img
            src="/assets/bender-cigar.png"
            alt="Bender"
            class="w-28 h-28 object-contain"
            style="filter: drop-shadow(0 0 16px rgba(212,130,10,0.4));"
          />
        </div>
      </div>

      <!-- Title -->
      <div class="text-center mb-1">
        <h1 class="font-display font-bold tracking-widest" style="
          font-size: 1.4rem;
          color: var(--accent);
          letter-spacing: 0.2em;
          text-shadow: 0 0 20px rgba(212,130,10,0.5);
        ">BENDERPI</h1>
      </div>
      <div class="text-center mb-7">
        <p class="font-display text-[9px] tracking-widest" style="color: var(--text-muted); letter-spacing: 0.2em;">CONTROL TERMINAL v3.0</p>
      </div>

      <!-- PIN input -->
      <input
        type="password"
        inputmode="numeric"
        pattern="[0-9]*"
        maxlength="16"
        bind:value={pin}
        on:keydown={handleKeydown}
        placeholder="· · · · ·"
        class="w-full rounded px-4 py-3 text-center text-xl tracking-widest transition-all outline-none"
        style="
          background: var(--bg-input);
          border: 1px solid {error ? 'var(--error)' : 'var(--border)'};
          color: var(--text);
          font-family: var(--font-mono);
          box-shadow: {pin.length > 0 ? '0 0 12px rgba(212,130,10,0.15)' : 'none'};
        "
        on:focus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 16px rgba(212,130,10,0.2)'; }}
        on:blur={e => { e.currentTarget.style.borderColor = error ? 'var(--error)' : 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
        disabled={loading}
      />

      {#if error}
        <p class="text-center text-[10px] mt-2 font-display tracking-wider" style="color: var(--error);">{error}</p>
      {/if}

      <!-- Login button -->
      <button
        on:click={handleLogin}
        disabled={loading || !pin.trim()}
        class="mt-4 w-full rounded py-3 font-display font-bold tracking-widest transition-all"
        style="
          background: {loading || !pin.trim() ? 'var(--bg-input)' : 'var(--accent)'};
          color: {loading || !pin.trim() ? 'var(--text-muted)' : '#0c0a08'};
          border: 1px solid {loading || !pin.trim() ? 'var(--border)' : 'var(--accent)'};
          font-size: 0.7rem;
          letter-spacing: 0.2em;
          box-shadow: {loading || !pin.trim() ? 'none' : '0 0 20px rgba(212,130,10,0.3)'};
          cursor: {loading || !pin.trim() ? 'not-allowed' : 'pointer'};
        "
      >
        {loading ? 'AUTHENTICATING...' : 'BITE MY SHINY METAL APP'}
      </button>
    </div>

    <!-- Status line -->
    <div class="mt-4 text-center font-display text-[9px] tracking-widest" style="color: var(--text-muted); letter-spacing: 0.15em;">
      PLANET EXPRESS · DELIVERY DEPT
    </div>
  </div>
</div>
