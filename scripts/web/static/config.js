/* ═══════════════════════════════════════════════════════
   BenderPi Web UI — Config & Service Control
   Reorganised groups, collapsible panels, select dropdowns
   ═══════════════════════════════════════════════════════ */

(function () {
  "use strict";

  var el = window.bender.el;
  var apiJson = window.bender.apiJson;

  // Pending restart banner
  var restartBannerEl = null;

  function showRestartBanner() {
    if (!restartBannerEl) return;
    restartBannerEl.classList.remove("hidden");
  }

  function hideRestartBanner() {
    if (!restartBannerEl) return;
    restartBannerEl.classList.add("hidden");
  }

  // Track original values for dirty detection
  var originalConfig = {};
  var originalWatchdog = {};
  var currentConfig = {};
  var currentWatchdog = {};

  // ── Field definitions ─────────────────────────────

  var CONFIG_GROUPS = [
    {
      title: "Voice",
      fields: [
        { key: "speech_rate", label: "Speech Pace", type: "range", step: 0.1, min: 0.5, max: 2.0,
          help: "Higher = slower speech, lower = faster. 1.0 is normal." },
        { key: "thinking_sound", label: "Thinking Sound", type: "bool" }
      ]
    },
    {
      title: "Audio",
      fields: [
        { key: "silence_pre", label: "Pre-silence (s)", type: "float", step: 0.01, min: 0, max: 1 },
        { key: "silence_post", label: "Post-silence (s)", type: "float", step: 0.01, min: 0, max: 1 },
        { key: "silence_timeout", label: "Silence Timeout (s)", type: "float", step: 0.5, min: 1, max: 30 }
      ]
    },
    {
      title: "STT (Speech-to-Text)",
      fields: [
        { key: "whisper_model", label: "Whisper Model", type: "string" },
        { key: "vad_aggressiveness", label: "VAD Aggressiveness", type: "int", step: 1, min: 0, max: 3 }
      ]
    },
    {
      title: "AI",
      fields: [
        { key: "ai_model", label: "AI Model", type: "string" },
        { key: "ai_max_tokens", label: "AI Max Tokens", type: "int", step: 1, min: 50, max: 4096 }
      ]
    },
    {
      title: "Conversation",
      fields: [
        { key: "simple_intent_max_words", label: "Simple Intent Max Words", type: "int", step: 1, min: 1, max: 20 }
      ]
    },
    {
      title: "Briefings",
      fields: [
        { key: "weather_ttl", label: "Weather TTL (s)", type: "int", step: 60, min: 60, max: 86400 },
        { key: "news_ttl", label: "News TTL (s)", type: "int", step: 60, min: 60, max: 86400 }
      ]
    },
    {
      title: "Logging",
      fields: [
        { key: "log_level", label: "Log Level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"] }
      ]
    },
    {
      title: "LEDs",
      fields: [
        { key: "led_brightness", label: "LED Brightness", type: "range", step: 0.1, min: 0, max: 1.0 },
        { key: "led_colour", label: "LED Colour (R, G, B)", type: "colour" },
        { key: "led_listening_colour", label: "Listening Colour", type: "colour" },
        { key: "led_talking_colour", label: "Talking Colour", type: "colour" },
        { key: "led_listening_enabled", label: "LED Listening Enabled", type: "bool",
          help: "Show LED colour while listening for wake word" },
        { key: "silent_wakeword", label: "Silent Wakeword", type: "bool",
          help: "Skip audio greeting on wake (requires LED Listening Enabled)",
          dependsOn: "led_listening_enabled" }
      ]
    }
  ];

  var WATCHDOG_FIELDS = [
    { key: "stt_empty_rate_threshold", label: "STT Empty Rate Threshold", help: "Max fraction of STT results that are empty" },
    { key: "api_fallback_rate_threshold", label: "API Fallback Rate Threshold", help: "Max fraction of turns using AI fallback" },
    { key: "error_rate_threshold", label: "Error Rate Threshold", help: "Max fraction of turns with errors" },
    { key: "stt_latency_threshold_ms", label: "STT Latency Threshold (ms)", help: "Max average STT latency" },
    { key: "tts_latency_threshold_ms", label: "TTS Latency Threshold (ms)", help: "Max average TTS latency" },
    { key: "api_latency_threshold_ms", label: "API Latency Threshold (ms)", help: "Max average API call latency" },
    { key: "promote_candidate_min_hits", label: "Promote Candidate Min Hits", help: "Min AI hits before suggesting promotion" },
    { key: "briefing_stale_weather_s", label: "Briefing Stale Weather (s)", help: "Max age before weather briefing is stale" },
    { key: "briefing_stale_news_s", label: "Briefing Stale News (s)", help: "Max age before news briefing is stale" },
    { key: "min_avg_session_turns", label: "Min Avg Session Turns", help: "Minimum expected avg turns per session" },
    { key: "log_gap_threshold_s", label: "Log Gap Threshold (s)", help: "Max seconds between log entries before alert" },
    { key: "lookback_hours", label: "Lookback Hours", help: "Hours of data to analyse" }
  ];

  // ── Helpers ─────────────────────────────────────

  function deepCopy(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function showFeedback(container, msg, isError) {
    var existing = container.querySelector(".cfg-feedback");
    if (existing) existing.remove();
    var fb = el("div", { className: "cfg-feedback " + (isError ? "error" : "success") }, [msg]);
    container.prepend(fb);
    setTimeout(function () { if (fb.parentNode) fb.remove(); }, 4000);
  }

  // ── Service Control Section ─────────────────────

  function buildServiceSection(panel) {
    var section = el("div", { className: "card cfg-service-section" });

    var header = el("div", { className: "card-header" }, [
      el("h2", { textContent: "Service Control" })
    ]);
    section.appendChild(header);

    // Status row
    var statusRow = el("div", { className: "cfg-status-row" });
    var statusBadge = el("span", { className: "badge badge-muted", textContent: "Checking..." });
    var uptimeSpan = el("span", { className: "text-muted cfg-uptime" });
    statusRow.appendChild(el("span", { textContent: "Service: " }));
    statusRow.appendChild(statusBadge);
    statusRow.appendChild(uptimeSpan);
    section.appendChild(statusRow);

    // Puppet Only toggle
    var toggleRow = el("div", { className: "cfg-toggle-row" });
    var toggleLabel = el("label", { textContent: "Puppet Only Mode", className: "cfg-toggle-label" });
    var toggleHelp = el("span", { className: "text-muted cfg-toggle-help", textContent: "When ON: stops bender-converse (no wake word). When OFF: normal mode." });
    var toggleSwitch = el("label", { className: "cfg-switch" });
    var toggleCheckbox = el("input", { type: "checkbox" });
    toggleCheckbox.type = "checkbox";
    var toggleSlider = el("span", { className: "cfg-switch-slider" });
    toggleSwitch.appendChild(toggleCheckbox);
    toggleSwitch.appendChild(toggleSlider);
    toggleRow.appendChild(toggleLabel);
    toggleRow.appendChild(toggleSwitch);
    toggleRow.appendChild(toggleHelp);
    section.appendChild(toggleRow);

    var puppetOnly = false;

    toggleCheckbox.addEventListener("change", function () {
      var newMode = toggleCheckbox.checked ? "puppet_only" : "converse";
      var confirmMsg = toggleCheckbox.checked
        ? "Stop bender-converse? Bender will no longer respond to wake word."
        : "Start bender-converse? Bender will resume normal operation.";
      if (!window.confirm(confirmMsg)) {
        toggleCheckbox.checked = !toggleCheckbox.checked;
        return;
      }
      toggleCheckbox.disabled = true;
      apiJson("/api/actions/toggle-mode", {
        method: "POST",
        body: JSON.stringify({ mode: newMode })
      }).then(function () {
        puppetOnly = toggleCheckbox.checked;
        showFeedback(section, "Mode set to: " + newMode, false);
        refreshStatus();
      }).catch(function (err) {
        toggleCheckbox.checked = !toggleCheckbox.checked;
        showFeedback(section, "Failed: " + err.message, true);
      }).finally(function () {
        toggleCheckbox.disabled = false;
      });
    });

    // Action buttons grid
    var actions = [
      { label: "Restart Bender", endpoint: "/api/actions/restart", confirm: "Restart bender-converse service?" },
      { label: "Refresh Briefings", endpoint: "/api/actions/refresh-briefings", confirm: "Refresh weather/news briefings? (restarts service)" },
      { label: "Rebuild Responses", endpoint: "/api/actions/prebuild", confirm: "Rebuild all pre-generated TTS responses?" },
      { label: "Generate Status", endpoint: "/api/actions/generate-status", confirm: "Regenerate status report?" }
    ];

    var grid = el("div", { className: "cfg-action-grid" });
    actions.forEach(function (act) {
      var btn = el("button", { className: "btn", textContent: act.label });
      btn.addEventListener("click", function () {
        if (!window.confirm(act.confirm)) return;
        btn.disabled = true;
        btn.textContent = "Running...";
        apiJson(act.endpoint, { method: "POST", body: "{}" }).then(function (data) {
          var msg = "Done";
          if (data.output) msg = "Done. Output: " + data.output.substring(0, 200);
          showFeedback(section, act.label + ": " + msg, false);
          refreshStatus();
        }).catch(function (err) {
          showFeedback(section, act.label + " failed: " + err.message, true);
        }).finally(function () {
          btn.disabled = false;
          btn.textContent = act.label;
        });
      });
      grid.appendChild(btn);
    });
    section.appendChild(grid);

    function refreshStatus() {
      apiJson("/api/actions/service-status").then(function (data) {
        if (data.running) {
          statusBadge.className = "badge badge-success";
          statusBadge.textContent = "Running";
          puppetOnly = false;
        } else {
          statusBadge.className = "badge badge-error";
          statusBadge.textContent = "Stopped";
          puppetOnly = true;
        }
        toggleCheckbox.checked = puppetOnly;
        uptimeSpan.textContent = data.uptime && data.uptime !== "unknown" ? " \u2014 " + data.uptime : "";
      }).catch(function () {
        statusBadge.className = "badge badge-muted";
        statusBadge.textContent = "Unknown";
      });
    }

    refreshStatus();
    return section;
  }

  // ── Config Editor Section ───────────────────────

  function buildFieldInput(fieldDef, value, onChange) {
    var type = fieldDef.type;

    if (type === "bool") {
      var sw = el("label", { className: "cfg-switch" });
      var cb = el("input", { type: "checkbox" });
      cb.type = "checkbox";
      cb.checked = !!value;
      cb.addEventListener("change", function () { onChange(cb.checked); });
      var slider = el("span", { className: "cfg-switch-slider" });
      sw.appendChild(cb);
      sw.appendChild(slider);
      return sw;
    }

    if (type === "select") {
      var select = el("select", { className: "config-select" });
      (fieldDef.options || []).forEach(function (opt) {
        var option = el("option", { value: opt, textContent: opt });
        if (opt === value) option.selected = true;
        select.appendChild(option);
      });
      select.addEventListener("change", function () { onChange(select.value); });
      return select;
    }

    if (type === "range") {
      var wrap = el("div", { className: "cfg-range-wrap" });
      var rangeInput = el("input", {
        type: "range",
        min: String(fieldDef.min),
        max: String(fieldDef.max),
        step: String(fieldDef.step),
        className: "cfg-range-slider"
      });
      rangeInput.type = "range";
      rangeInput.value = value != null ? String(value) : String(fieldDef.min);
      var rangeLabel = el("span", { className: "cfg-range-value", textContent: String(rangeInput.value) });
      rangeInput.addEventListener("input", function () {
        rangeLabel.textContent = rangeInput.value;
        onChange(parseFloat(rangeInput.value));
      });
      wrap.appendChild(rangeInput);
      wrap.appendChild(rangeLabel);
      return wrap;
    }

    if (type === "colour") {
      var colWrap = el("div", { className: "cfg-colour-wrap" });
      var rgb = Array.isArray(value) ? value : [255, 120, 0];
      var swatch = el("div", { className: "cfg-colour-swatch" });
      swatch.style.backgroundColor = "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";

      var labels = ["R", "G", "B"];
      var inputs = [];
      labels.forEach(function (lbl, i) {
        var inp = el("input", {
          type: "number",
          min: "0",
          max: "255",
          step: "1",
          className: "cfg-colour-input"
        });
        inp.type = "number";
        inp.value = String(rgb[i]);
        inp.addEventListener("input", function () {
          var r = parseInt(inputs[0].value, 10) || 0;
          var g = parseInt(inputs[1].value, 10) || 0;
          var b = parseInt(inputs[2].value, 10) || 0;
          swatch.style.backgroundColor = "rgb(" + r + "," + g + "," + b + ")";
          onChange([r, g, b]);
        });
        inputs.push(inp);
        var labelEl = el("span", { className: "cfg-colour-label", textContent: lbl });
        colWrap.appendChild(labelEl);
        colWrap.appendChild(inp);
      });
      colWrap.appendChild(swatch);
      return colWrap;
    }

    if (type === "int") {
      var numInput = el("input", {
        type: "number",
        step: String(fieldDef.step || 1),
        min: fieldDef.min != null ? String(fieldDef.min) : "",
        max: fieldDef.max != null ? String(fieldDef.max) : ""
      });
      numInput.type = "number";
      numInput.value = value != null ? String(value) : "";
      numInput.addEventListener("input", function () {
        onChange(parseInt(numInput.value, 10));
      });
      return numInput;
    }

    if (type === "float") {
      var fInput = el("input", {
        type: "number",
        step: String(fieldDef.step || 0.01),
        min: fieldDef.min != null ? String(fieldDef.min) : "",
        max: fieldDef.max != null ? String(fieldDef.max) : ""
      });
      fInput.type = "number";
      fInput.value = value != null ? String(value) : "";
      fInput.addEventListener("input", function () {
        onChange(parseFloat(fInput.value));
      });
      return fInput;
    }

    // Default: string
    var sInput = el("input", { type: "text" });
    sInput.type = "text";
    sInput.value = value != null ? String(value) : "";
    sInput.addEventListener("input", function () {
      onChange(sInput.value);
    });
    return sInput;
  }

  function buildConfigEditor(panel) {
    var section = el("div", { className: "card cfg-editor-section" });
    var header = el("div", { className: "card-header" }, [
      el("h2", { textContent: "Bender Configuration" })
    ]);
    section.appendChild(header);

    var formBody = el("div", { className: "cfg-form-body" });
    section.appendChild(formBody);

    var saveRow = el("div", { className: "cfg-save-row" });
    var saveBtn = el("button", { className: "btn btn-primary", textContent: "Save Configuration" });
    var changesInfo = el("span", { className: "text-muted cfg-changes-info" });
    saveRow.appendChild(saveBtn);
    saveRow.appendChild(changesInfo);
    section.appendChild(saveRow);

    // Track dependent field wrappers for live updates
    var dependentFields = {};

    function getChangedFields() {
      var changed = {};
      for (var key in currentConfig) {
        var curr = currentConfig[key];
        var orig = originalConfig[key];
        if (JSON.stringify(curr) !== JSON.stringify(orig)) {
          changed[key] = curr;
        }
      }
      return changed;
    }

    function updateChangesInfo() {
      var changed = getChangedFields();
      var keys = Object.keys(changed);
      if (keys.length === 0) {
        changesInfo.textContent = "No changes";
        saveBtn.disabled = true;
      } else {
        changesInfo.textContent = keys.length + " field(s) changed: " + keys.join(", ");
        saveBtn.disabled = false;
      }
    }

    function updateDependentFields() {
      // silent_wakeword depends on led_listening_enabled
      for (var depKey in dependentFields) {
        var info = dependentFields[depKey];
        var parentVal = !!currentConfig[info.dependsOn];
        if (parentVal) {
          info.wrapper.classList.remove("cfg-field-disabled");
          info.note.textContent = "";
        } else {
          info.wrapper.classList.add("cfg-field-disabled");
          info.note.textContent = "Requires " + info.dependsOnLabel + " to be enabled";
        }
      }
    }

    function renderForm(config) {
      formBody.textContent = "";
      dependentFields = {};

      CONFIG_GROUPS.forEach(function (group) {
        var details = el("details", { className: "cfg-group", open: "" });
        var summary = el("summary", { className: "cfg-group-title" }, [
          document.createTextNode(group.title)
        ]);
        details.appendChild(summary);

        var fieldsGrid = el("div", { className: "cfg-fields-grid" });
        group.fields.forEach(function (fieldDef) {
          if (!(fieldDef.key in config)) return;
          var fieldWrap = el("div", { className: "cfg-field" });
          var lbl = el("label", { textContent: fieldDef.label });
          fieldWrap.appendChild(lbl);

          if (fieldDef.help) {
            var helpEl = el("span", { className: "text-muted cfg-field-help", textContent: fieldDef.help });
            fieldWrap.appendChild(helpEl);
          }

          // Dependency note placeholder
          var depNote = el("span", { className: "text-muted cfg-field-help" });
          if (fieldDef.dependsOn) {
            fieldWrap.appendChild(depNote);
          }

          var input = buildFieldInput(fieldDef, config[fieldDef.key], function (val) {
            currentConfig[fieldDef.key] = val;
            updateChangesInfo();
            updateDependentFields();
          });
          fieldWrap.appendChild(input);
          fieldsGrid.appendChild(fieldWrap);

          // Register dependent fields
          if (fieldDef.dependsOn) {
            // Find the parent field label
            var parentLabel = fieldDef.dependsOn;
            for (var gi = 0; gi < CONFIG_GROUPS.length; gi++) {
              for (var fi = 0; fi < CONFIG_GROUPS[gi].fields.length; fi++) {
                if (CONFIG_GROUPS[gi].fields[fi].key === fieldDef.dependsOn) {
                  parentLabel = CONFIG_GROUPS[gi].fields[fi].label;
                }
              }
            }
            dependentFields[fieldDef.key] = {
              wrapper: fieldWrap,
              note: depNote,
              dependsOn: fieldDef.dependsOn,
              dependsOnLabel: parentLabel
            };
          }
        });
        details.appendChild(fieldsGrid);
        formBody.appendChild(details);
      });
      updateChangesInfo();
      updateDependentFields();
    }

    saveBtn.addEventListener("click", function () {
      var changed = getChangedFields();
      if (Object.keys(changed).length === 0) return;
      var msg = "Save changes?\n\n" + Object.keys(changed).map(function (k) {
        return k + ": " + JSON.stringify(originalConfig[k]) + " -> " + JSON.stringify(changed[k]);
      }).join("\n");
      if (!window.confirm(msg)) return;
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";
      apiJson("/api/config", {
        method: "PUT",
        body: JSON.stringify(changed)
      }).then(function (data) {
        originalConfig = deepCopy(data.config);
        currentConfig = deepCopy(data.config);
        renderForm(currentConfig);
        showFeedback(section, "Configuration saved", false);
        showRestartBanner();
      }).catch(function (err) {
        showFeedback(section, "Save failed: " + err.message, true);
      }).finally(function () {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save Configuration";
        updateChangesInfo();
      });
    });

    // Load config
    apiJson("/api/config").then(function (data) {
      originalConfig = deepCopy(data);
      currentConfig = deepCopy(data);
      renderForm(currentConfig);
    }).catch(function (err) {
      formBody.appendChild(el("p", { className: "error", textContent: "Failed to load config: " + err.message }));
    });

    return section;
  }

  // ── Watchdog Config Section ─────────────────────

  function buildWatchdogEditor(panel) {
    var section = el("div", { className: "card cfg-editor-section" });
    var header = el("div", { className: "card-header" }, [
      el("h2", { textContent: "Watchdog Thresholds" })
    ]);
    section.appendChild(header);

    var formBody = el("div", { className: "cfg-form-body" });
    section.appendChild(formBody);

    var saveRow = el("div", { className: "cfg-save-row" });
    var saveBtn = el("button", { className: "btn btn-primary", textContent: "Save Watchdog Config" });
    var changesInfo = el("span", { className: "text-muted cfg-changes-info" });
    saveRow.appendChild(saveBtn);
    saveRow.appendChild(changesInfo);
    section.appendChild(saveRow);

    function getChangedFields() {
      var changed = {};
      for (var key in currentWatchdog) {
        if (JSON.stringify(currentWatchdog[key]) !== JSON.stringify(originalWatchdog[key])) {
          changed[key] = currentWatchdog[key];
        }
      }
      return changed;
    }

    function updateChangesInfo() {
      var changed = getChangedFields();
      var keys = Object.keys(changed);
      if (keys.length === 0) {
        changesInfo.textContent = "No changes";
        saveBtn.disabled = true;
      } else {
        changesInfo.textContent = keys.length + " field(s) changed: " + keys.join(", ");
        saveBtn.disabled = false;
      }
    }

    function renderForm(config) {
      formBody.textContent = "";
      var details = el("details", { className: "cfg-group", open: "" });
      var summary = el("summary", { className: "cfg-group-title" }, [
        document.createTextNode("Watchdog Thresholds")
      ]);
      details.appendChild(summary);

      var fieldsGrid = el("div", { className: "cfg-fields-grid" });
      WATCHDOG_FIELDS.forEach(function (fieldDef) {
        if (!(fieldDef.key in config)) return;
        var fieldWrap = el("div", { className: "cfg-field" });
        var lbl = el("label", { textContent: fieldDef.label });
        fieldWrap.appendChild(lbl);
        if (fieldDef.help) {
          var helpEl = el("span", { className: "text-muted cfg-field-help", textContent: fieldDef.help });
          fieldWrap.appendChild(helpEl);
        }
        var val = config[fieldDef.key];
        var isFloat = typeof val === "number" && val % 1 !== 0;
        var inp = el("input", {
          type: "number",
          step: isFloat ? "0.01" : "1",
          min: "0"
        });
        inp.type = "number";
        inp.value = val != null ? String(val) : "";
        inp.addEventListener("input", function () {
          currentWatchdog[fieldDef.key] = isFloat ? parseFloat(inp.value) : parseInt(inp.value, 10);
          updateChangesInfo();
        });
        fieldWrap.appendChild(inp);
        fieldsGrid.appendChild(fieldWrap);
      });
      details.appendChild(fieldsGrid);
      formBody.appendChild(details);
      updateChangesInfo();
    }

    saveBtn.addEventListener("click", function () {
      var changed = getChangedFields();
      if (Object.keys(changed).length === 0) return;
      var msg = "Save watchdog changes?\n\n" + Object.keys(changed).map(function (k) {
        return k + ": " + JSON.stringify(originalWatchdog[k]) + " -> " + JSON.stringify(changed[k]);
      }).join("\n");
      if (!window.confirm(msg)) return;
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";
      apiJson("/api/config/watchdog", {
        method: "PUT",
        body: JSON.stringify(changed)
      }).then(function (data) {
        originalWatchdog = deepCopy(data.config);
        currentWatchdog = deepCopy(data.config);
        renderForm(currentWatchdog);
        showFeedback(section, "Watchdog config saved", false);
        showRestartBanner();
      }).catch(function (err) {
        showFeedback(section, "Save failed: " + err.message, true);
      }).finally(function () {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save Watchdog Config";
        updateChangesInfo();
      });
    });

    // Load watchdog config
    apiJson("/api/config/watchdog").then(function (data) {
      originalWatchdog = deepCopy(data);
      currentWatchdog = deepCopy(data);
      renderForm(currentWatchdog);
    }).catch(function (err) {
      formBody.appendChild(el("p", { className: "error", textContent: "Failed to load watchdog config: " + err.message }));
    });

    return section;
  }

  // ── Init ────────────────────────────────────────

  var initialised = false;

  function initConfig() {
    var panel = document.getElementById("panel-config");
    if (initialised) return;
    initialised = true;
    panel.textContent = "";

    // Pending restart banner
    restartBannerEl = el("div", { className: "cfg-restart-banner hidden" });
    var bannerIcon = el("span", { className: "cfg-restart-banner-icon", textContent: "\u26A0" });
    var bannerText = el("span", { className: "cfg-restart-banner-text",
      textContent: "Changes saved \u2014 restart required to apply." });
    var applyBtn = el("button", { className: "btn btn-warning cfg-restart-apply-btn",
      textContent: "Apply Now" });
    applyBtn.addEventListener("click", function () {
      applyBtn.disabled = true;
      applyBtn.textContent = "Restarting\u2026";
      apiJson("/api/actions/restart", { method: "POST", body: "{}" }).then(function () {
        hideRestartBanner();
        showFeedback(document.getElementById("panel-config"), "Service restarted \u2014 changes applied.", false);
      }).catch(function (err) {
        showFeedback(document.getElementById("panel-config"), "Restart failed: " + err.message, true);
      }).finally(function () {
        applyBtn.disabled = false;
        applyBtn.textContent = "Apply Now";
      });
    });
    restartBannerEl.appendChild(bannerIcon);
    restartBannerEl.appendChild(bannerText);
    restartBannerEl.appendChild(applyBtn);
    panel.appendChild(restartBannerEl);

    panel.appendChild(buildServiceSection(panel));
    panel.appendChild(buildConfigEditor(panel));
    panel.appendChild(buildWatchdogEditor(panel));
  }

  window.bender.onTabInit("config", initConfig);

})();
