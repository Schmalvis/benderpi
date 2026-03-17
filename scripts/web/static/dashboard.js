/* ═══════════════════════════════════════════════════════
   BenderPi Web UI — Dashboard Tab
   Status banner, health, performance, usage, alerts,
   recent errors, git log — collapsible themed cards
   ═══════════════════════════════════════════════════════ */

(function () {
  "use strict";

  var apiJson = window.bender.apiJson;
  var el = window.bender.el;
  var getQuote = window.bender.getQuote;

  var panel = null;
  var refreshBtn = null;
  var contentArea = null;
  var statusBanner = null;
  var statusText = null;
  var statusQuote = null;
  var sessionPollId = null;

  // ── Init ─────────────────────────────────────────────

  function initDashboard() {
    panel = document.getElementById("panel-dashboard");
    if (!panel) return;

    panel.textContent = "";
    buildShell();
    loadStatus();
    startStatusPoll();
  }

  function buildShell() {
    // Status banner
    statusBanner = el("div", { className: "card dashboard-section dashboard-status-banner" });
    statusText = el("span", { className: "dashboard-status-text", textContent: "Idle" });
    statusQuote = el("span", { className: "text-muted dashboard-status-quote", textContent: getQuote("dashboard") });
    statusBanner.appendChild(el("div", { className: "card-header" }, [
      el("h3", { textContent: "Bender Status" })
    ]));
    statusBanner.appendChild(el("div", { className: "dashboard-row" }, [
      statusText,
      statusQuote
    ]));
    panel.appendChild(statusBanner);

    var header = el("div", { className: "card-header" }, [
      el("h2", { textContent: "Dashboard" }),
      (function () {
        refreshBtn = el("button", { className: "btn btn-sm", onClick: handleRefresh });
        refreshBtn.appendChild(document.createTextNode("Refresh"));
        return refreshBtn;
      }())
    ]);
    panel.appendChild(header);

    contentArea = el("div", { className: "dashboard-content" });
    panel.appendChild(contentArea);
  }

  // ── Status Polling ──────────────────────────────────

  function startStatusPoll() {
    stopStatusPoll();
    pollStatus();
    sessionPollId = setInterval(pollStatus, 3000);
  }

  function stopStatusPoll() {
    if (sessionPollId) {
      clearInterval(sessionPollId);
      sessionPollId = null;
    }
  }

  function pollStatus() {
    apiJson("/api/actions/session-status").then(function (data) {
      if (!statusText) return;
      if (data.active) {
        var turn = data.turn || 0;
        statusText.textContent = "In Conversation (turn " + turn + ")";
        statusText.className = "dashboard-status-text success";
      } else {
        // Check service status
        apiJson("/api/actions/service-status").then(function (svc) {
          if (!statusText) return;
          if (!svc.running) {
            statusText.textContent = "Puppet Only Mode";
            statusText.className = "dashboard-status-text warning";
          } else {
            statusText.textContent = "Idle";
            statusText.className = "dashboard-status-text";
          }
        }).catch(function () {});
      }
    }).catch(function () {
      if (statusText) {
        statusText.textContent = "Unknown";
        statusText.className = "dashboard-status-text text-muted";
      }
    });
  }

  // ── Load / Refresh ──────────────────────────────────

  function handleRefresh() {
    setRefreshing(true);
    apiJson("/api/status/refresh", { method: "POST" })
      .then(function (data) {
        setRefreshing(false);
        renderAll(data);
      })
      .catch(function (err) {
        setRefreshing(false);
        showError("Refresh failed: " + err.message);
      });
  }

  function loadStatus() {
    showLoading();
    apiJson("/api/status")
      .then(function (data) {
        renderAll(data);
      })
      .catch(function (err) {
        showError("Could not load status: " + err.message);
      });
  }

  function setRefreshing(active) {
    if (!refreshBtn) return;
    refreshBtn.disabled = active;
    refreshBtn.textContent = "";
    if (active) {
      refreshBtn.appendChild(el("span", { className: "spinner" }));
    } else {
      refreshBtn.appendChild(document.createTextNode("Refresh"));
    }
  }

  function showLoading() {
    if (!contentArea) return;
    contentArea.textContent = "";
    contentArea.appendChild(
      el("div", { className: "dashboard-loading" }, [
        el("span", { className: "spinner spinner--lg" }),
        el("span", { className: "text-muted", textContent: "Loading status..." })
      ])
    );
  }

  function showError(msg) {
    if (!contentArea) return;
    contentArea.textContent = "";
    contentArea.appendChild(
      el("p", { className: "error", textContent: msg })
    );
  }

  // ── Render All ──────────────────────────────────────

  function renderAll(data) {
    if (!contentArea) return;
    contentArea.textContent = "";

    contentArea.appendChild(renderHealthRow(data.health, data.alerts));
    contentArea.appendChild(renderPerformanceRow(data.performance));
    contentArea.appendChild(renderUsageSection(data.usage));
    contentArea.appendChild(renderAlertsSection(data.alerts));
    contentArea.appendChild(renderRecentErrors(data.recent_errors));
    contentArea.appendChild(renderGitLog(data.git_log));
  }

  // ── Health Row ──────────────────────────────────────

  function renderHealthRow(health, alerts) {
    var errors7d = health.errors_7d;
    var alertCount = health.alert_count;

    var overallStatus, overallColour;
    if (errors7d === 0 && alertCount === 0) {
      overallStatus = "Healthy";
      overallColour = "success";
    } else {
      var hasErrorAlert = (alerts || []).some(function (a) { return a.severity === "error"; });
      if (hasErrorAlert || errors7d > 5) {
        overallStatus = "Degraded";
        overallColour = "error";
      } else {
        overallStatus = "Warning";
        overallColour = "warning";
      }
    }

    var row = el("div", { className: "dashboard-row dashboard-health-row" });

    row.appendChild(metricCard(
      "Errors (7d)",
      String(errors7d),
      errors7d === 0 ? "success" : errors7d < 5 ? "warning" : "error"
    ));

    row.appendChild(metricCard(
      "Active Alerts",
      String(alertCount),
      alertCount === 0 ? "success" : alertCount < 3 ? "warning" : "error"
    ));

    row.appendChild(metricCard(
      "Overall Health",
      overallStatus,
      overallColour
    ));

    return wrapSection(null, row);
  }

  // ── Performance Row ─────────────────────────────────

  function renderPerformanceRow(perf) {
    var metrics = [
      { label: "STT Record",     key: "stt_record_ms" },
      { label: "STT Transcribe", key: "stt_transcribe_ms" },
      { label: "TTS Generate",   key: "tts_generate_ms" },
      { label: "API Call",       key: "ai_api_call_ms" },
      { label: "Audio Play",     key: "audio_play_ms" },
      { label: "End-to-end",     key: "response_total_ms" },
    ];

    var row = el("div", { className: "dashboard-row dashboard-perf-row" });

    metrics.forEach(function (m) {
      var val = perf[m.key];
      var display = (val !== null && val !== undefined) ? Math.round(val) + "ms" : "N/A";

      var colour;
      if (val === null || val === undefined) {
        colour = "muted";
      } else if (m.key === "response_total_ms") {
        colour = val > 3000 ? "error" : val > 1500 ? "warning" : "success";
      } else {
        colour = val > 1000 ? "error" : val > 500 ? "warning" : "success";
      }
      row.appendChild(metricCard(m.label, display, colour, "mono"));
    });

    return wrapSection("Performance (7-day avg)", row);
  }

  // ── Usage Section ───────────────────────────────────

  function renderUsageSection(usage) {
    var section = el("div", { className: "card dashboard-section" });

    section.appendChild(el("div", { className: "card-header" }, [
      el("h3", { textContent: "Usage (7 days)" })
    ]));

    var summaryRow = el("div", { className: "dashboard-row" });
    summaryRow.appendChild(metricCard("Sessions",  String(usage.sessions), "muted"));
    summaryRow.appendChild(metricCard("Turns",     String(usage.turns), "muted"));
    summaryRow.appendChild(metricCard("Local",     usage.local + " (" + usage.local_pct + "%)", "success"));
    summaryRow.appendChild(metricCard("API Calls", String(usage.api), "muted"));
    summaryRow.appendChild(metricCard("Errors",    String(usage.errors), usage.errors === 0 ? "success" : "error"));
    section.appendChild(summaryRow);

    // Top intents bar chart
    var intents = usage.top_intents || {};
    var intentKeys = Object.keys(intents);

    var intentSub = el("div", { className: "dashboard-sub" }, [
      el("h4", { className: "dashboard-sub-title text-muted", textContent: "Top Intents" })
    ]);

    if (intentKeys.length === 0) {
      intentSub.appendChild(el("p", { className: "text-muted", textContent: "No intent data." }));
    } else {
      var maxVal = Math.max.apply(null, intentKeys.map(function (k) { return intents[k]; }));
      var list = el("ul", { className: "dashboard-intent-list" });
      intentKeys.forEach(function (intent) {
        var count = intents[intent];
        var pct = maxVal > 0 ? Math.round(100 * count / maxVal) : 0;
        var bar = el("div", { className: "dashboard-intent-bar" });
        bar.style.width = pct + "%";
        list.appendChild(el("li", { className: "dashboard-intent-item" }, [
          el("span", { className: "dashboard-intent-name", textContent: intent }),
          el("div", { className: "dashboard-intent-bar-wrap" }, [bar]),
          el("span", { className: "dashboard-intent-count mono", textContent: String(count) })
        ]));
      });
      intentSub.appendChild(list);
    }
    section.appendChild(intentSub);

    return section;
  }

  // ── Alerts Section (collapsible) ────────────────────

  function renderAlertsSection(alerts) {
    var details = el("details", { className: "card dashboard-section" });

    var summary = el("summary", { className: "card-header" }, [
      el("h3", { textContent: "Watchdog Alerts" }),
      el("span", {
        className: "badge " + (alerts.length === 0 ? "badge-success" : "badge-warning"),
        textContent: String(alerts.length)
      })
    ]);
    details.appendChild(summary);

    if (!alerts || alerts.length === 0) {
      details.appendChild(
        el("p", { className: "text-muted", textContent: "No alerts. All systems nominal." })
      );
      return details;
    }

    var list = el("ul", { className: "dashboard-alert-list" });
    alerts.forEach(function (a) {
      var badgeClass = {
        "error":   "badge-error",
        "warning": "badge-warning",
        "info":    "badge-muted"
      }[a.severity] || "badge-muted";

      list.appendChild(el("li", { className: "dashboard-alert-item" }, [
        el("span", { className: "badge " + badgeClass, textContent: a.severity.toUpperCase() }),
        el("span", { className: "dashboard-alert-check text-muted", textContent: "[" + a.check + "]" }),
        el("span", { className: "dashboard-alert-msg", textContent: a.message })
      ]));
    });
    details.appendChild(list);

    return details;
  }

  // ── Recent Errors (collapsible) ─────────────────────

  function renderRecentErrors(errors) {
    var details = el("details", { className: "card dashboard-section" });

    var summary = el("summary", { className: "card-header" }, [
      el("h3", { textContent: "Recent Errors" })
    ]);
    details.appendChild(summary);

    if (!errors || errors.length === 0) {
      details.appendChild(
        el("p", { className: "text-muted", textContent: "No recent errors." })
      );
      return details;
    }

    var list = el("ul", { className: "dashboard-error-list" });
    errors.forEach(function (line) {
      list.appendChild(
        el("li", { className: "dashboard-error-line mono", textContent: line })
      );
    });
    details.appendChild(list);

    return details;
  }

  // ── Git Log (collapsible) ───────────────────────────

  function renderGitLog(gitLog) {
    var details = el("details", { className: "card dashboard-section" });

    var summary = el("summary", { className: "card-header" }, [
      el("h3", { textContent: "Recent Changes" })
    ]);
    details.appendChild(summary);

    var text = (gitLog || "").trim() || "(no git log available)";
    var pre = el("pre", { className: "dashboard-git-log mono" });
    pre.textContent = text;
    details.appendChild(pre);

    return details;
  }

  // ── Helpers ─────────────────────────────────────────

  function metricCard(label, value, colour, extra) {
    var colourClass = {
      "success": "dashboard-metric--success",
      "warning": "dashboard-metric--warning",
      "error":   "dashboard-metric--error",
      "muted":   "dashboard-metric--muted"
    }[colour] || "dashboard-metric--muted";

    var valueClass = "dashboard-metric-value " + colourClass + (extra ? " " + extra : "");

    return el("div", { className: "card dashboard-metric" }, [
      el("div", { className: "dashboard-metric-label text-muted", textContent: label }),
      el("div", { className: valueClass, textContent: value })
    ]);
  }

  function wrapSection(title, content) {
    var section = el("div", { className: "card dashboard-section" });
    if (title) {
      section.appendChild(el("div", { className: "card-header" }, [
        el("h3", { textContent: title })
      ]));
    }
    section.appendChild(content);
    return section;
  }

  // ── Register ─────────────────────────────────────────

  window.bender.onTabInit("dashboard", initDashboard);

}());
