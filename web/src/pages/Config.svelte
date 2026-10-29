<script>
  import { onMount } from 'svelte';
  import { config, savedConfig, isDirty, loadConfig, saveConfig, resetConfig } from '../lib/stores/config.js';
  import { getWatchdogConfig, updateWatchdogConfig,
           rebuildResponses, generateStatus, getVolume, setVolume, restartService } from '../lib/api.js';
  import { toast } from '../lib/stores/toast.js';

  let saving = false;
  let saveMsg = '';
  let restartRequired = false;
  let watchdog = {};
  let watchdogDirty = false;

  onMount(async () => {
    await loadConfig();
    try {
      watchdog = await getWatchdogConfig();
    } catch { /* ignore */ }
  });

  async function handleSave() {
    saving = true;
    saveMsg = '';
    try {
      const result = await saveConfig();
      if (result && result.restart_required) restartRequired = true;
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

  let restarting = false;
  async function handleRestartNow() {
    restarting = true;
    try {
      await restartService();
      toast.push('Service restarting…', 'success');
      restartRequired = false;
    } catch (e) {
      toast.push('Restart failed: ' + (e.message || 'unknown error'), 'error');
    }
    restarting = false;
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

  // Halloween mode. Two config flags drive the night, and `wake_converse.py`
  // reads them in two different places:
  //   halloween_mode           -> _halloween_loop instead of the wake-word loop
  //   presence_sensor_enabled  -> the distance gate inside that loop
  // Neither has any effect until bender-converse restarts, because `cfg` is an
  // import-time singleton.
  $: halloweenOn = $config.halloween_mode === true;
  $: gateOn = $config.presence_sensor_enabled === true;

  // The two flags that need a restart to mean anything. Comparing against the
  // SAVED copy, not a local touched-flag: a switch flipped and flipped back is
  // not pending, and a save made on another tab is already applied.
  $: halloweenPending =
       $config.halloween_mode !== $savedConfig.halloween_mode ||
       $config.presence_sensor_enabled !== $savedConfig.presence_sensor_enabled;

  let applyingHalloween = false;

  async function handleApplyHalloween() {
    // Save and restart as ONE action. Two separate steps is how the mode got
    // flipped in the UI and left inert on the device: `cfg` is read once at
    // process start, so a saved flag with no restart changes nothing at all.
    if (applyingHalloween) return;
    applyingHalloween = true;
    const wanted = $config.halloween_mode === true;
    try {
      await saveConfig();
      if (watchdogDirty) {
        await updateWatchdogConfig(watchdog);
        watchdogDirty = false;
      }
      await restartService();
      restartRequired = false;
      saveMsg = '';
      toast.push(wanted
        ? 'Halloween mode is live — Bender restarted, wake word off'
        : 'Normal mode is live — Bender restarted, wake word on', 'success');
    } catch (e) {
      toast.push('Could not apply: ' + (e.message || 'unknown error'), 'error');
    }
    applyingHalloween = false;
  }

  function setHalloweenMode(on) {
    config.update(c => {
      const next = { ...c, halloween_mode: on };
      // One switch sets both. An open mic with no distance gate starts 88.6
      // sessions/hour from ordinary room speech (measured 2026-10-10 on 5.25h
      // of recordings) and leaves Bender in a session 79% of the time, so the
      // child who actually arrives is ignored for 22-45s. The gate fails open
      // when the sensor is missing or broken, so coupling it to the mode can
      // never cause silence. Clear the checkbox below to decouple them.
      if (on && c.presence_sensor_enabled !== true) next.presence_sensor_enabled = true;
      return next;
    });
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

  // Maintenance actions
  let maintenance = { rebuild: false, status: false };
  let maintenanceMsg = '';
  let volume = 50;

  onMount(async () => {
    try { const v = await getVolume(); volume = v.volume ?? 50; } catch { /* ignore */ }
  });

  async function handleRebuild() {
    maintenance.rebuild = true;
    maintenanceMsg = '';
    try {
      const r = await rebuildResponses();
      maintenanceMsg = r.status === 'ok' ? 'Responses rebuilt.' : (r.output || 'Done.');
    } catch (e) { maintenanceMsg = 'Error: ' + e.message; }
    maintenance.rebuild = false;
  }

  async function handleGenerateStatus() {
    maintenance.status = true;
    maintenanceMsg = '';
    try {
      await generateStatus();
      maintenanceMsg = 'STATUS.md regenerated.';
    } catch (e) { maintenanceMsg = 'Error: ' + e.message; }
    maintenance.status = false;
  }

  async function handleVolume(e) {
    volume = parseInt(e.target.value);
    try { await setVolume(volume); } catch { /* ignore */ }
  }

</script>

<div class="max-w-2xl space-y-4 pb-24">

  {#if restartRequired}
    <div class="bg-warning/10 border border-warning text-warning rounded-lg px-4 py-3 flex items-center justify-between gap-3 text-sm">
      <span>Config saved — bender-converse won't pick this up until it restarts (it reads config once at startup).</span>
      <button
        class="bg-warning text-bg-base font-medium px-3 py-1.5 rounded whitespace-nowrap hover:opacity-90 disabled:opacity-50 transition-opacity"
        disabled={restarting} on:click={handleRestartNow}>
        {restarting ? 'Restarting…' : 'Restart Now'}
      </button>
    </div>
  {/if}

  <!-- Halloween mode -->
  <details open class="bg-bg-card border rounded-lg overflow-hidden {halloweenOn ? 'border-warning' : 'border-border'}">
    <summary class={summaryClass + ' px-4'}>Halloween</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div class="flex gap-2">
        <button
          type="button"
          class="flex-1 px-3 py-2 rounded text-sm font-medium transition-colors {halloweenOn ? 'bg-bg-input text-text-muted border border-border' : 'bg-accent text-bg-base'}"
          on:click={() => setHalloweenMode(false)}>
          Normal
        </button>
        <button
          type="button"
          class="flex-1 px-3 py-2 rounded text-sm font-medium transition-colors {halloweenOn ? 'bg-warning text-bg-base' : 'bg-bg-input text-text-muted border border-border'}"
          on:click={() => setHalloweenMode(true)}>
          Halloween
        </button>
      </div>

      {#if halloweenPending || restartRequired}
        <div class="bg-warning/10 border border-warning rounded px-3 py-2.5 space-y-2">
          <p class="text-sm font-medium text-warning">
            {halloweenOn ? 'Halloween mode is not live yet.' : 'Normal mode is not live yet.'}
          </p>
          <p class="text-xs" style="color: var(--text-muted);">
            bender-converse reads the config once, at startup. The switch does nothing
            until it restarts.
          </p>
          <button
            type="button"
            class="w-full bg-warning text-bg-base font-medium px-3 py-2 rounded text-sm
                   hover:opacity-90 disabled:opacity-50 transition-opacity"
            disabled={applyingHalloween}
            on:click={handleApplyHalloween}>
            {applyingHalloween ? 'Saving and restarting…' : 'Save & Restart Bender'}
          </button>
        </div>
      {/if}

      {#if halloweenOn}
        <div class="bg-warning/10 border border-warning text-warning rounded px-3 py-2 text-sm space-y-1">
          <p class="font-medium">The wake word is off in Halloween mode.</p>
          <p class="text-[11px] opacity-90">
            Any nearby speech starts a session. Replies are local only, capped
            at {$config.halloween_max_sentences ?? 2} sentences and
            {$config.halloween_max_turns ?? 6} turns.
          </p>
          <p class="text-[11px] opacity-90">
            Halloween mode refuses to start if the local model is unavailable.
            It falls back to the wake-word loop and logs HALLOWEEN MODE REFUSED,
            rather than speaking an error line to a child.
          </p>
        </div>
      {/if}

      <div class="flex items-center gap-3">
        <input
          id="presence_sensor_enabled"
          type="checkbox"
          class="w-4 h-4 accent-accent"
          bind:checked={$config.presence_sensor_enabled}
        />
        <label for="presence_sensor_enabled" class="text-text-default text-sm">
          Presence gate (VL53L0X distance sensor)
        </label>
      </div>

      {#if halloweenOn && !gateOn}
        <div class="bg-error/10 border border-error text-error rounded px-3 py-2 text-[11px] space-y-1">
          <p class="font-medium">No distance gate. Bender will talk to the room.</p>
          <p class="opacity-90">
            Measured 2026-10-10 on 5.25h of recorded speech: 88.6 sessions/hour,
            in a session 79% of the time. Each false session makes Bender deaf
            for 22-45s, so the child who actually arrives is ignored.
          </p>
        </div>
      {/if}

      {#if gateOn}
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class={labelClass} for="presence_max_mm">Gate distance (mm)</label>
            <input id="presence_max_mm" type="number" min="0" max="8000" step="50"
                   class={inputClass} bind:value={$config.presence_max_mm} />
            <p class="text-[11px] text-text-muted mt-1">Opens at or under this</p>
          </div>
          <div>
            <label class={labelClass} for="presence_clear_mm">Release distance (mm)</label>
            <input id="presence_clear_mm" type="number" min="0" max="8000" step="50"
                   class={inputClass} bind:value={$config.presence_clear_mm} />
            <p class="text-[11px] text-text-muted mt-1">Must exceed the gate</p>
          </div>
        </div>

        {#if $config.presence_clear_mm !== undefined && $config.presence_max_mm !== undefined
             && Number($config.presence_clear_mm) <= Number($config.presence_max_mm)}
          <p class="text-[11px] text-error">
            Release must be greater than the gate, or someone standing on the
            boundary flaps the mic open and shut. The save will be refused.
          </p>
        {/if}

        <div>
          <label class={labelClass} for="presence_confirm_reads">Confirming reads</label>
          <input id="presence_confirm_reads" type="number" min="1" max="20" step="1"
                 class={inputClass} bind:value={$config.presence_confirm_reads} />
          <p class="text-[11px] text-text-muted mt-1">
            Agreeing readings before the gate changes state. Measured 2026-10-13:
            one read costs about 0.21s, so 2 arms the gate in 0.41s.
          </p>
        </div>

        <p class="text-[11px] text-text-muted">
          Measured on this build 2026-10-13: tracked to 1267mm, 0 errors in 435
          reads, no crosstalk. Re-run
          <code class="text-accent">scripts/probe_vl53l0x.py --xshut 17 --long-range --baseline</code>
          after any change to the mounting.
        </p>
      {/if}

      <details class="border-t border-border pt-2">
        <summary class={summaryClass}>Session limits</summary>
        <div class="grid grid-cols-2 gap-3 pt-2">
          <div>
            <label class={labelClass} for="halloween_max_turns">Max turns</label>
            <input id="halloween_max_turns" type="number" min="1" max="20" step="1"
                   class={inputClass} bind:value={$config.halloween_max_turns} />
          </div>
          <div>
            <label class={labelClass} for="halloween_idle_timeout_s">Idle timeout (s)</label>
            <input id="halloween_idle_timeout_s" type="number" min="1" max="30" step="0.5"
                   class={inputClass} bind:value={$config.halloween_idle_timeout_s} />
          </div>
          <div>
            <label class={labelClass} for="halloween_cooldown_s">Cooldown (s)</label>
            <input id="halloween_cooldown_s" type="number" min="0" max="30" step="0.5"
                   class={inputClass} bind:value={$config.halloween_cooldown_s} />
          </div>
          <div>
            <label class={labelClass} for="halloween_max_sentences">Max sentences</label>
            <input id="halloween_max_sentences" type="number" min="1" max="10" step="1"
                   class={inputClass} bind:value={$config.halloween_max_sentences} />
          </div>
          <div class="col-span-2">
            <label class={labelClass} for="halloween_max_tokens">Max tokens</label>
            <input id="halloween_max_tokens" type="number" min="8" max="400" step="8"
                   class={inputClass} bind:value={$config.halloween_max_tokens} />
            <p class="text-[11px] text-text-muted mt-1">
              Shorter is better at the door. At the measured 5.6-6.9 tok/s, 48
              tokens is about 7s of decode.
            </p>
          </div>
        </div>
      </details>

    </div>
  </details>

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

  <!-- Everyday settings. Hidden in Halloween mode: none of them apply at the
       door, and a smaller surface on the night is the point. General,
       Maintenance and Watchdog stay, because speech rate, a rebuild and the
       health thresholds are all still useful.
       There was a Vision section here too. Every control in it was inert:
       Passive Mode called three routes deleted on 2026-04-02, and the
       Confidence Threshold and Detection Allowlist were YOLO settings that
       nothing has read since 0d0d4ff replaced YOLO with the Qwen2-VL VLM.
       Both keys passed the config schema on extra="allow", so the page wrote
       them to bender_config.json and no code ever read them back. -->
  {#if !halloweenOn}

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

  {/if}

  <!-- Maintenance -->
  <details open class="bg-bg-card border border-border rounded-lg overflow-hidden">
    <summary class={summaryClass + ' px-4'}>Maintenance</summary>
    <div class="px-4 pb-4 pt-2 space-y-4">

      <div>
        <label class={labelClass}>Volume — <span class="text-text-default">{volume}</span></label>
        <input type="range" min="0" max="100" step="1" class="w-full accent-accent"
          bind:value={volume} on:change={handleVolume} />
        <div class="flex justify-between text-[11px] text-text-muted mt-1">
          <span>Mute (0)</span><span>Full (100)</span>
        </div>
      </div>

      <div class="flex flex-wrap gap-2">
        <button
          class="bg-bg-input border border-border text-text-default px-4 py-2 rounded text-sm font-medium hover:border-accent transition-colors disabled:opacity-40"
          disabled={maintenance.rebuild} on:click={handleRebuild}>
          {maintenance.rebuild ? 'Rebuilding…' : 'Rebuild Responses'}
        </button>
        <button
          class="bg-bg-input border border-border text-text-default px-4 py-2 rounded text-sm font-medium hover:border-accent transition-colors disabled:opacity-40"
          disabled={maintenance.status} on:click={handleGenerateStatus}>
          {maintenance.status ? 'Generating…' : 'Generate Status'}
        </button>
      </div>
      {#if maintenanceMsg}
        <p class="text-sm {maintenanceMsg.startsWith('Error') ? 'text-error' : 'text-success'}">{maintenanceMsg}</p>
      {/if}

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
