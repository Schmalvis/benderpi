<script>
  import { onMount } from 'svelte';
  import { config, isDirty, loadConfig, saveConfig, resetConfig } from '../lib/stores/config.js';
  import { getWatchdogConfig, updateWatchdogConfig, getVisionPassive, enableVisionPassive, disableVisionPassive } from '../lib/api.js';

  let saving = false;
  let saveMsg = '';
  let watchdog = {};
  let watchdogDirty = false;

  // Vision passive mode state
  let visionPassive = { enabled: false, expires_at: null, minutes_remaining: null };
  let visionError = '';
  let visionPollInterval = null;

  const visionPresets = [
    { label: '15m', minutes: 15 },
    { label: '30m', minutes: 30 },
    { label: '1h', minutes: 60 },
    { label: '3h', minutes: 180 },
    { label: '∞', minutes: null },
  ];

  onMount(async () => {
    await loadConfig();
    try {
      watchdog = await getWatchdogConfig();
    } catch { /* ignore */ }
    await loadVisionPassive();
    visionPollInterval = setInterval(loadVisionPassive, 60000);
    return () => clearInterval(visionPollInterval);
  });

  async function loadVisionPassive() {
    try {
      visionPassive = await getVisionPassive();
      visionError = '';
    } catch (e) {
      visionError = e.message;
    }
  }

  async function handleVisionEnable(minutes) {
    try {
      await enableVisionPassive(minutes);
      await loadVisionPassive();
      visionError = '';
    } catch (e) {
      visionError = e.message;
    }
  }

  async function handleVisionDisable() {
    try {
      await disableVisionPassive();
      await loadVisionPassive();
      visionError = '';
    } catch (e) {
      visionError = e.message;
    }
  }

  function activePreset() {
    if (!visionPassive.enabled) return null;
    if (visionPassive.minutes_remaining === null) return null; // indefinite
    return visionPassive.minutes_remaining;
  }

  async function handleSave() {
    saving = true;
    saveMsg = '';
    try {
      await saveConfig();
      if (watchdogDirty) {
        await updateWatchdogConfig(watchdog);
        watchdogDirty = false;
      }
      saveMsg = 'Saved!';
      setTimeout(() => saveMsg = '', 2000);
    } catch (e) {
      saveMsg = 'Error: ' + e.message;
    }
    saving = false;
  }

  function handleReset() {
    resetConfig();
    saveMsg = '';
  }

  function markWatchdogDirty() {
    watchdogDirty = true;
  }

  // Helper to get/set nested ai_routing values reactively
  function getRouting(key) {
    return $config.ai_routing?.[key] ?? 'local_first';
  }

  function setRouting(key, value) {
    config.update(c => ({
      ...c,
      ai_routing: { ...(c.ai_routing ?? {}), [key]: value }
    }));
  }

  // ha_room_synonyms is an object — edit as JSON text
  let synonymsText = '';
  let synonymsError = '';

  $: {
    if ($config.ha_room_synonyms !== undefined) {
      try {
        const current = JSON.stringify($config.ha_room_synonyms, null, 2);
        if (current !== synonymsText) synonymsText = current;
      } catch { /* ignore */ }
    }
  }

  function handleSynonymsChange(e) {
    synonymsText = e.target.value;
    try {
      const parsed = JSON.parse(synonymsText);
      synonymsError = '';
      config.update(c => ({ ...c, ha_room_synonyms: parsed }));
    } catch {
      synonymsError = 'Invalid JSON';
    }
  }

  const routingOptions = [
    { value: 'local_first', label: 'Local First' },
    { value: 'local_only', label: 'Local Only' },
    { value: 'cloud_only', label: 'Cloud Only' },
  ];

  const backendOptions = [
    { value: 'hybrid', label: 'Hybrid' },
    { value: 'local_only', label: 'Local Only' },
    { value: 'cloud_only', label: 'Cloud Only' },
  ];

  const inputClass = 'bg-bg-input border border-border rounded px-3 py-2 text-text-default focus:outline-none focus:border-accent transition-colors w-full';
  const labelClass = 'block text-[11px] text-text-muted uppercase tracking-wider mb-1';
  const summaryClass = 'cursor-pointer text-[11px] text-text-muted uppercase tracking-wider font-semibold select-none py-2';
</script>

<div class="max-w-2xl space-y-4 pb-24">

  <!-- General -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>General</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <label class={labelClass} for="speech_rate">Speech Rate</label>
        <input
          id="speech_rate"
          type="number"
          min="0.5" max="2.0" step="0.1"
          class={inputClass}
          bind:value={$config.speech_rate}
        />
        <p class="text-[11px] text-text-muted mt-1">0.5 (slow) to 2.0 (fast), default 1.0</p>
      </div>

      <div class="flex items-center gap-3">
        <input
          id="dismissal_ends_session"
          type="checkbox"
          class="w-4 h-4 accent-accent"
          bind:checked={$config.dismissal_ends_session}
        />
        <label for="dismissal_ends_session" class="text-text-default text-sm">Dismissal ends session</label>
      </div>

    </div>
  </details>

  <!-- AI Backend -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>AI Backend</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <label class={labelClass} for="ai_backend">Backend Mode</label>
        <select id="ai_backend" class={inputClass} bind:value={$config.ai_backend}>
          {#each backendOptions as opt}
            <option value={opt.value}>{opt.label}</option>
          {/each}
        </select>
      </div>

      <div>
        <label class={labelClass} for="local_llm_model">Local LLM Model</label>
        <input id="local_llm_model" type="text" class={inputClass} bind:value={$config.local_llm_model} placeholder="e.g. llama3.2" />
      </div>

      <div>
        <label class={labelClass} for="local_llm_url">Local LLM URL</label>
        <input id="local_llm_url" type="text" class={inputClass} bind:value={$config.local_llm_url} placeholder="http://localhost:11434" />
      </div>

      <div>
        <label class={labelClass} for="local_llm_timeout">Local LLM Timeout (s)</label>
        <input id="local_llm_timeout" type="number" min="1" max="15" class={inputClass} bind:value={$config.local_llm_timeout} />
      </div>

      <div class="border-t border-border pt-4">
        <p class={labelClass + ' mb-3'}>AI Routing</p>
        <div class="space-y-3">
          <div>
            <label class={labelClass} for="routing_conversation">Conversation</label>
            <select id="routing_conversation" class={inputClass}
              value={getRouting('conversation')}
              on:change={e => setRouting('conversation', e.target.value)}>
              {#each routingOptions as opt}
                <option value={opt.value}>{opt.label}</option>
              {/each}
            </select>
          </div>
          <div>
            <label class={labelClass} for="routing_knowledge">Knowledge</label>
            <select id="routing_knowledge" class={inputClass}
              value={getRouting('knowledge')}
              on:change={e => setRouting('knowledge', e.target.value)}>
              {#each routingOptions as opt}
                <option value={opt.value}>{opt.label}</option>
              {/each}
            </select>
          </div>
          <div>
            <label class={labelClass} for="routing_creative">Creative</label>
            <select id="routing_creative" class={inputClass}
              value={getRouting('creative')}
              on:change={e => setRouting('creative', e.target.value)}>
              {#each routingOptions as opt}
                <option value={opt.value}>{opt.label}</option>
              {/each}
            </select>
          </div>
        </div>
      </div>

    </div>
  </details>

  <!-- Home Assistant -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>Home Assistant</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <label class={labelClass} for="ha_url">HA URL</label>
        <input id="ha_url" type="text" class={inputClass} bind:value={$config.ha_url} placeholder="http://homeassistant.local:8123" />
      </div>

      <div>
        <label class={labelClass} for="ha_token">HA Token</label>
        <input id="ha_token" type="password" class={inputClass} bind:value={$config.ha_token} placeholder="Long-lived access token" />
      </div>

      <div>
        <label class={labelClass} for="ha_weather_entity">Weather Entity</label>
        <input id="ha_weather_entity" type="text" class={inputClass} bind:value={$config.ha_weather_entity} placeholder="weather.forecast_home" />
      </div>

      <div>
        <label class={labelClass} for="ha_room_synonyms">Room Synonyms (JSON)</label>
        <textarea
          id="ha_room_synonyms"
          rows="6"
          class="{inputClass} font-mono text-sm resize-y"
          value={synonymsText}
          on:input={handleSynonymsChange}
        ></textarea>
        {#if synonymsError}
          <p class="text-error text-[11px] mt-1">{synonymsError}</p>
        {/if}
      </div>

    </div>
  </details>

  <!-- Vision -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>Vision</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <p class={labelClass}>Passive Mode</p>
        <div class="flex flex-wrap gap-2 mt-1">
          <button
            on:click={handleVisionDisable}
            class="px-3 py-1.5 rounded text-sm font-medium transition-colors
                   {!visionPassive.enabled
                     ? 'bg-accent text-bg'
                     : 'bg-bg-input border border-border text-text-muted hover:border-accent hover:text-text-default'}"
          >
            OFF
          </button>
          {#each visionPresets as preset}
            <button
              on:click={() => handleVisionEnable(preset.minutes)}
              class="px-3 py-1.5 rounded text-sm font-medium transition-colors
                     {visionPassive.enabled && (
                       preset.minutes === null
                         ? visionPassive.minutes_remaining === null
                         : visionPassive.minutes_remaining !== null
                     )
                       ? 'bg-accent text-bg'
                       : 'bg-bg-input border border-border text-text-muted hover:border-accent hover:text-text-default'}"
            >
              {preset.label}
            </button>
          {/each}
        </div>
        {#if visionPassive.enabled}
          <p class="text-[11px] text-text-muted mt-2">
            {#if visionPassive.minutes_remaining !== null}
              Time remaining: {visionPassive.minutes_remaining} minute{visionPassive.minutes_remaining === 1 ? '' : 's'}
            {:else}
              Active indefinitely
            {/if}
          </p>
        {/if}
        {#if visionError}
          <p class="text-error text-[11px] mt-1">{visionError}</p>
        {/if}
      </div>

    </div>
  </details>

  <!-- LED -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>LED</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <label class={labelClass} for="led_brightness">
          Brightness — <span class="text-text-default">{$config.led_brightness ?? 128}</span>
        </label>
        <input
          id="led_brightness"
          type="range"
          min="0" max="255" step="1"
          class="w-full accent-accent"
          bind:value={$config.led_brightness}
        />
        <div class="flex justify-between text-[11px] text-text-muted mt-1">
          <span>Off (0)</span>
          <span>Full (255)</span>
        </div>
      </div>

      <div>
        <label class={labelClass} for="led_listening_color">Listening Color (hex)</label>
        <div class="flex gap-2 items-center">
          <input id="led_listening_color" type="text" class="{inputClass} font-mono" bind:value={$config.led_listening_color} placeholder="#00aaff" />
          <input type="color" class="h-10 w-10 rounded border border-border bg-bg-input cursor-pointer flex-shrink-0"
            value={$config.led_listening_color ?? '#00aaff'}
            on:input={e => config.update(c => ({ ...c, led_listening_color: e.target.value }))} />
        </div>
      </div>

      <div>
        <label class={labelClass} for="led_talking_color">Talking Color (hex)</label>
        <div class="flex gap-2 items-center">
          <input id="led_talking_color" type="text" class="{inputClass} font-mono" bind:value={$config.led_talking_color} placeholder="#ff6600" />
          <input type="color" class="h-10 w-10 rounded border border-border bg-bg-input cursor-pointer flex-shrink-0"
            value={$config.led_talking_color ?? '#ff6600'}
            on:input={e => config.update(c => ({ ...c, led_talking_color: e.target.value }))} />
        </div>
      </div>

    </div>
  </details>

  <!-- Watchdog -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>Watchdog</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <label class={labelClass} for="error_rate_threshold">Error Rate Threshold</label>
        <input
          id="error_rate_threshold"
          type="number"
          min="0" step="0.01"
          class={inputClass}
          bind:value={watchdog.error_rate_threshold}
          on:input={markWatchdogDirty}
        />
      </div>

      <div>
        <label class={labelClass} for="lookback_hours">Lookback Hours</label>
        <input
          id="lookback_hours"
          type="number"
          min="1"
          class={inputClass}
          bind:value={watchdog.lookback_hours}
          on:input={markWatchdogDirty}
        />
      </div>

    </div>
  </details>

</div>

<!-- Sticky save bar -->
{#if $isDirty || watchdogDirty}
  <div class="sticky bottom-0 bg-bg-card border-t border-border p-4 flex items-center gap-3">
    <button on:click={handleSave} disabled={saving}
      class="bg-accent text-bg font-bold px-6 py-2 rounded hover:opacity-90 disabled:opacity-50 transition-opacity">
      {saving ? 'Saving...' : 'Save Changes'}
    </button>
    <button on:click={handleReset}
      class="bg-bg-input border border-border text-text-muted px-6 py-2 rounded hover:text-text-default transition-colors">
      Reset
    </button>
    {#if saveMsg}
      <span class="text-sm {saveMsg.startsWith('Error') ? 'text-error' : 'text-success'}">{saveMsg}</span>
    {/if}
  </div>
{/if}
