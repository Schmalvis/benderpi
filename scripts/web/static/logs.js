/* ═══════════════════════════════════════════════════════
   BenderPi Web UI — Log Viewer Tab
   ═══════════════════════════════════════════════════════ */

(function () {
  "use strict";

  var el = window.bender.el;
  var apiJson = window.bender.apiJson;
  var apiDownload = window.bender.apiDownload;

  // ── State ────────────────────────────────────────────

  var state = {
    view: "conversations",      // conversations | system | metrics
    convDate: null,
    systemLines: [],
    systemLevel: "",
    systemSearch: "",
    metricsName: "response_total",
    metricsHours: 24,
  };

  // ── Root container ───────────────────────────────────

  var root = null;

  // ── Initialise tab ───────────────────────────────────

  function initLogs() {
    root = document.getElementById("panel-logs");
    if (!root) return;
    // Clear and rebuild
    while (root.firstChild) root.removeChild(root.firstChild);
    buildShell();
    switchView(state.view);
  }

  // ── Shell: sub-view toggle buttons ──────────────────

  function buildShell() {
    var header = el("div", { className: "logs-header" }, [
      buildNavBtn("conversations", "Conversations"),
      buildNavBtn("system", "System Log"),
      buildNavBtn("metrics", "Metrics"),
    ]);
    root.appendChild(header);

    var body = el("div", { id: "logs-body", className: "logs-body" });
    root.appendChild(body);
  }

  function buildNavBtn(view, label) {
    var btn = el("button", {
      className: "btn btn-sm" + (state.view === view ? " btn-primary" : ""),
      textContent: label,
      onClick: function () { switchView(view); },
    });
    btn.dataset.logsNav = view;
    return btn;
  }

  function switchView(view) {
    state.view = view;
    // Update nav button states
    var navBtns = root.querySelectorAll("[data-logs-nav]");
    navBtns.forEach(function (btn) {
      var active = btn.dataset.logsNav === view;
      btn.className = "btn btn-sm" + (active ? " btn-primary" : "");
    });
    var body = document.getElementById("logs-body");
    if (!body) return;
    while (body.firstChild) body.removeChild(body.firstChild);
    if (view === "conversations") renderConversations(body);
    else if (view === "system") renderSystem(body);
    else if (view === "metrics") renderMetrics(body);
  }

  // ══════════════════════════════════════════════════════
  // CONVERSATIONS VIEW
  // ══════════════════════════════════════════════════════

  function renderConversations(body) {
    // Date picker row
    var dateRow = el("div", { className: "logs-date-row" });
    var today = new Date();
    for (var i = 0; i < 7; i++) {
      var d = new Date(today);
      d.setDate(today.getDate() - i);
      var dateStr = formatDate(d);
      (function (ds) {
        var btn = el("button", {
          className: "btn btn-sm" + (state.convDate === ds ? " btn-primary" : ""),
          textContent: i === 0 ? "Today" : ds,
          onClick: function () { loadConvDate(ds, body); },
        });
        btn.dataset.convDate = ds;
        dateRow.appendChild(btn);
      })(dateStr);
    }
    body.appendChild(dateRow);

    var content = el("div", { id: "conv-content" });
    body.appendChild(content);

    // Downloads section
    body.appendChild(buildDownloads());

    // Auto-load first date
    if (!state.convDate) {
      loadConvDate(formatDate(today), body);
    } else {
      loadConvDate(state.convDate, body);
    }
  }

  function loadConvDate(dateStr, body) {
    state.convDate = dateStr;
    // Update date button states
    var dateBtns = body.querySelectorAll("[data-conv-date]");
    dateBtns.forEach(function (btn) {
      var active = btn.dataset.convDate === dateStr;
      btn.className = "btn btn-sm" + (active ? " btn-primary" : "");
    });
    var content = document.getElementById("conv-content");
    if (!content) return;
    while (content.firstChild) content.removeChild(content.firstChild);
    content.appendChild(el("div", { className: "logs-loading" }, [
      el("span", { className: "spinner" }),
      el("span", { textContent: " Loading " + dateStr + "..." }),
    ]));

    apiJson("/api/logs/conversations/" + dateStr)
      .then(function (data) {
        while (content.firstChild) content.removeChild(content.firstChild);
        renderSessions(content, data.events || []);
      })
      .catch(function (err) {
        while (content.firstChild) content.removeChild(content.firstChild);
        var msg = err.message || String(err);
        if (msg.indexOf("404") !== -1) {
          content.appendChild(el("p", { className: "text-muted logs-empty", textContent: "No log for " + dateStr + "." }));
        } else {
          content.appendChild(el("p", { className: "error", textContent: "Error: " + msg }));
        }
      });
  }

  function renderSessions(container, events) {
    if (!events.length) {
      container.appendChild(el("p", { className: "text-muted logs-empty", textContent: "No events recorded." }));
      return;
    }
    // Group events by session_id
    var sessions = {};
    var order = [];
    events.forEach(function (ev) {
      var sid = ev.session_id || "unknown";
      if (!sessions[sid]) {
        sessions[sid] = { start: null, end: null, turns: [] };
        order.push(sid);
      }
      if (ev.type === "session_start") sessions[sid].start = ev;
      else if (ev.type === "session_end") sessions[sid].end = ev;
      else if (ev.type === "turn") sessions[sid].turns.push(ev);
    });

    order.forEach(function (sid) {
      var sess = sessions[sid];
      container.appendChild(buildSessionCard(sid, sess));
    });
  }

  function buildSessionCard(sid, sess) {
    var startTs = sess.start ? (sess.start.ts || "") : "";
    var turnCount = sess.turns.length;
    var endReason = sess.end ? (sess.end.reason || "ended") : "unknown";
    var durationText = "";
    if (sess.end && sess.end.duration_s != null) {
      durationText = sess.end.duration_s + "s";
    }

    var summary = el("summary", { className: "session-summary" }, [
      el("span", { className: "session-id mono", textContent: sid.slice(0, 8) }),
      el("span", { className: "session-meta text-muted", textContent: formatTs(startTs) }),
      el("span", { className: "badge badge-muted", textContent: turnCount + " turn" + (turnCount !== 1 ? "s" : "") }),
      el("span", { className: "badge badge-muted", textContent: endReason }),
      durationText ? el("span", { className: "badge badge-muted", textContent: durationText }) : null,
    ]);

    var turnsDiv = el("div", { className: "session-turns" });
    if (sess.turns.length) {
      sess.turns.forEach(function (turn) {
        turnsDiv.appendChild(buildTurnRow(turn));
      });
    } else {
      turnsDiv.appendChild(el("p", { className: "text-muted", textContent: "No turns recorded." }));
    }

    return el("details", { className: "session-card" }, [summary, turnsDiv]);
  }

  function buildTurnRow(turn) {
    var intent = turn.intent || "";
    var method = turn.method || "";
    var userText = turn.user_text || turn.text || "";
    var responseText = turn.response_text || turn.response || "";

    var methodClass = "method-" + method;
    var row = el("div", { className: "turn-row" }, [
      el("div", { className: "turn-user" }, [
        el("span", { className: "turn-label text-muted", textContent: "You: " }),
        el("span", { textContent: userText }),
      ]),
      el("div", { className: "turn-meta" }, [
        intent ? el("span", { className: "badge badge-muted turn-badge", textContent: intent }) : null,
        method ? el("span", { className: "badge turn-badge " + methodClass, textContent: method }) : null,
      ]),
      responseText ? el("div", { className: "turn-response" }, [
        el("span", { className: "turn-label text-muted", textContent: "Bender: " }),
        el("span", { textContent: responseText }),
      ]) : null,
    ]);
    return row;
  }

  // ══════════════════════════════════════════════════════
  // SYSTEM LOG VIEW
  // ══════════════════════════════════════════════════════

  function renderSystem(body) {
    // Controls row
    var controls = el("div", { className: "logs-controls" }, [
      buildLevelBtn("", "ALL"),
      buildLevelBtn("INFO", "INFO"),
      buildLevelBtn("WARNING", "WARNING"),
      buildLevelBtn("ERROR", "ERROR"),
    ]);

    var searchInput = el("input", {
      type: "search",
      className: "logs-search",
      placeholder: "Search log lines…",
    });
    searchInput.addEventListener("input", function () {
      state.systemSearch = searchInput.value;
      refreshSystemDisplay();
    });
    searchInput.value = state.systemSearch;

    var refreshBtn = el("button", {
      className: "btn btn-sm",
      textContent: "Refresh",
      onClick: function () { fetchSystemLog(logContainer); },
    });

    var toolbar = el("div", { className: "logs-toolbar" }, [controls, searchInput, refreshBtn]);
    body.appendChild(toolbar);

    var logContainer = el("div", { id: "system-log-container", className: "log-block logs-system-block" });
    body.appendChild(logContainer);

    fetchSystemLog(logContainer);
  }

  function buildLevelBtn(level, label) {
    var active = state.systemLevel === level;
    var btn = el("button", {
      className: "btn btn-sm" + (active ? " btn-primary" : ""),
      textContent: label,
      onClick: function () { setSystemLevel(level); },
    });
    btn.dataset.sysLevel = level;
    return btn;
  }

  function setSystemLevel(level) {
    state.systemLevel = level;
    // Update button states
    var btns = document.querySelectorAll("[data-sys-level]");
    btns.forEach(function (btn) {
      var active = btn.dataset.sysLevel === level;
      btn.className = "btn btn-sm" + (active ? " btn-primary" : "");
    });
    refreshSystemDisplay();
  }

  function fetchSystemLog(container) {
    while (container.firstChild) container.removeChild(container.firstChild);
    container.appendChild(el("span", { textContent: "Loading…" }));

    var url = "/api/logs/system?lines=500";
    if (state.systemLevel) url += "&level=" + encodeURIComponent(state.systemLevel);

    apiJson(url)
      .then(function (data) {
        state.systemLines = data.lines || [];
        refreshSystemDisplay();
      })
      .catch(function (err) {
        while (container.firstChild) container.removeChild(container.firstChild);
        container.appendChild(el("span", { className: "error", textContent: "Error: " + (err.message || err) }));
      });
  }

  function refreshSystemDisplay() {
    var container = document.getElementById("system-log-container");
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    var lines = state.systemLines;
    if (state.systemLevel) {
      lines = lines.filter(function (ln) { return ln.indexOf(state.systemLevel) !== -1; });
    }
    if (state.systemSearch) {
      var q = state.systemSearch.toLowerCase();
      lines = lines.filter(function (ln) { return ln.toLowerCase().indexOf(q) !== -1; });
    }

    if (!lines.length) {
      container.appendChild(el("span", { className: "text-muted", textContent: "No lines to display." }));
      return;
    }

    lines.forEach(function (ln) {
      var lineEl = el("div", { className: "log-line" });
      // Colour-code by level
      if (ln.indexOf("ERROR") !== -1 || ln.indexOf("CRITICAL") !== -1) {
        lineEl.classList.add("log-line--error");
      } else if (ln.indexOf("WARNING") !== -1) {
        lineEl.classList.add("log-line--warning");
      } else if (ln.indexOf("DEBUG") !== -1) {
        lineEl.classList.add("log-line--debug");
      }
      lineEl.textContent = ln;
      container.appendChild(lineEl);
    });
    // Scroll to bottom
    container.scrollTop = container.scrollHeight;
  }

  // ══════════════════════════════════════════════════════
  // METRICS VIEW
  // ══════════════════════════════════════════════════════

  var METRIC_NAMES = [
    "response_total", "stt_record", "stt_transcribe", "stt_empty",
    "tts_generate", "ai_api_call", "api_call", "audio_play",
    "intent", "error", "session",
  ];

  var TIME_RANGES = [
    { label: "1h", hours: 1 },
    { label: "6h", hours: 6 },
    { label: "24h", hours: 24 },
    { label: "7d", hours: 168 },
  ];

  function renderMetrics(body) {
    // Metric name dropdown
    var nameSelect = el("select", { className: "logs-metrics-select" });
    METRIC_NAMES.forEach(function (n) {
      var opt = el("option", { value: n, textContent: n });
      if (n === state.metricsName) opt.selected = true;
      nameSelect.appendChild(opt);
    });
    nameSelect.addEventListener("change", function () {
      state.metricsName = nameSelect.value;
      fetchMetrics(tableBody);
    });

    // Time range buttons
    var timeRow = el("div", { className: "logs-controls" });
    TIME_RANGES.forEach(function (tr) {
      var btn = el("button", {
        className: "btn btn-sm" + (state.metricsHours === tr.hours ? " btn-primary" : ""),
        textContent: tr.label,
        onClick: function () { setMetricsHours(tr.hours, tableBody); },
      });
      btn.dataset.metricsHours = tr.hours;
      timeRow.appendChild(btn);
    });

    var toolbar = el("div", { className: "logs-toolbar" }, [nameSelect, timeRow]);
    body.appendChild(toolbar);

    var tableWrap = el("div", { className: "logs-table-wrap" });
    var table = el("table");
    var thead = el("thead", {}, [
      el("tr", {}, [
        el("th", { textContent: "Timestamp" }),
        el("th", { textContent: "Name" }),
        el("th", { textContent: "Duration (ms)" }),
        el("th", { textContent: "Tags / Value" }),
      ]),
    ]);
    var tableBody = el("tbody");
    table.appendChild(thead);
    table.appendChild(tableBody);
    tableWrap.appendChild(table);
    body.appendChild(tableWrap);

    fetchMetrics(tableBody);
  }

  function setMetricsHours(hours, tableBody) {
    state.metricsHours = hours;
    var btns = document.querySelectorAll("[data-metrics-hours]");
    btns.forEach(function (btn) {
      var active = parseInt(btn.dataset.metricsHours, 10) === hours;
      btn.className = "btn btn-sm" + (active ? " btn-primary" : "");
    });
    fetchMetrics(tableBody);
  }

  function fetchMetrics(tableBody) {
    while (tableBody.firstChild) tableBody.removeChild(tableBody.firstChild);
    var loadRow = el("tr", {}, [
      el("td", { colSpan: "4", textContent: "Loading…" }),
    ]);
    tableBody.appendChild(loadRow);

    var url = "/api/logs/metrics?name=" + encodeURIComponent(state.metricsName) +
              "&hours=" + state.metricsHours;

    apiJson(url)
      .then(function (data) {
        while (tableBody.firstChild) tableBody.removeChild(tableBody.firstChild);
        var events = data.events || [];
        if (!events.length) {
          tableBody.appendChild(el("tr", {}, [
            el("td", { colSpan: "4", className: "text-muted", textContent: "No events in this time window." }),
          ]));
          return;
        }
        // Show most-recent first
        var reversed = events.slice().reverse();
        reversed.forEach(function (ev) {
          var durationMs = ev.duration_ms != null ? String(ev.duration_ms) : "";
          var tags = ev.tags != null
            ? (typeof ev.tags === "object" ? JSON.stringify(ev.tags) : String(ev.tags))
            : (ev.value != null ? String(ev.value) : "");
          tableBody.appendChild(el("tr", {}, [
            el("td", { className: "mono", textContent: formatTs(ev.ts || "") }),
            el("td", { textContent: ev.name || "" }),
            el("td", { className: "mono", textContent: durationMs }),
            el("td", { className: "mono", textContent: tags }),
          ]));
        });
      })
      .catch(function (err) {
        while (tableBody.firstChild) tableBody.removeChild(tableBody.firstChild);
        tableBody.appendChild(el("tr", {}, [
          el("td", { colSpan: "4", className: "error", textContent: "Error: " + (err.message || err) }),
        ]));
      });
  }

  // ══════════════════════════════════════════════════════
  // DOWNLOADS SECTION
  // ══════════════════════════════════════════════════════

  function buildDownloads() {
    var section = el("div", { className: "logs-downloads card" });
    section.appendChild(el("h3", { textContent: "Download Log Files" }));

    var listEl = el("div", { className: "logs-download-list" });
    section.appendChild(listEl);

    apiJson("/api/logs/conversations?days=30")
      .then(function (data) {
        var files = data.files || [];
        if (!files.length) {
          listEl.appendChild(el("p", { className: "text-muted", textContent: "No log files found." }));
          return;
        }
        files.forEach(function (f) {
          var sizeText = f.size > 1024
            ? (Math.round(f.size / 1024)) + " KB"
            : f.size + " B";
          var row = el("div", { className: "logs-download-row" }, [
            el("span", { className: "mono", textContent: f.filename }),
            el("span", { className: "text-muted", textContent: sizeText }),
            el("button", {
              className: "btn btn-sm",
              textContent: "Download",
              onClick: function () {
                apiDownload("/api/logs/download/" + encodeURIComponent(f.filename), f.filename);
              },
            }),
          ]);
          listEl.appendChild(row);
        });
      })
      .catch(function () {
        listEl.appendChild(el("p", { className: "error", textContent: "Could not load file list." }));
      });

    return section;
  }

  // ── Utilities ────────────────────────────────────────

  function formatDate(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function formatTs(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleTimeString();
    } catch (e) {
      return ts;
    }
  }

  // ── Register ─────────────────────────────────────────

  window.bender.onTabInit("logs", initLogs);

})();
