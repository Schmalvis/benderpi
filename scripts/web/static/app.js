/* ═══════════════════════════════════════════════════════
   BenderPi Web UI — Core Application
   Login, tabs, theme toggle, API helpers,
   sidebar controls, session polling, quotes, FAB logic
   ═══════════════════════════════════════════════════════ */

(function () {
  "use strict";

  // ── Constants ──────────────────────────────────────

  var PIN_KEY = "benderpi_pin";
  var THEME_KEY = "benderpi_theme";

  // ── Bender Quotes ─────────────────────────────────

  var BENDER_QUOTES = {
    footer: [
      "I'm 40% user interface!",
      "Bite my shiny metal dashboard.",
      "You know what cheers me up? Other people's misfortune.",
      "I'm so embarrassed. I wish everybody else was dead.",
    ],
    puppet: [
      "Shut up baby, I know it.",
      "Have you ever tried just turning off the TV?",
      "My voice is my passport. Verify me.",
      "Bite my shiny metal microphone.",
    ],
    dashboard: [
      "I'm 40% diagnostic panel!",
      "Compare your lives to mine and then kill yourselves.",
      "Well, we're boned.",
      "This is gonna be fun on a bun!",
    ],
    logs: [
      "This is the worst kind of discrimination - the kind against me!",
      "Memories. You're talking about memories.",
      "Save it for the memoir, meatbag.",
      "I choose to not remember that.",
    ],
    config: [
      "I'll build my own config, with blackjack and hookers.",
      "Bite my shiny metal preferences.",
      "I choose to believe what I was programmed to believe.",
      "Have you tried turning it off and on again? Oh wait, that's my job.",
    ],
    empty: [
      "Nothing to see here, meatbag.",
      "Bender was probably napping.",
      "File not found. I blame the humans.",
    ],
  };

  function getQuote(category) {
    var list = BENDER_QUOTES[category] || BENDER_QUOTES.footer;
    return list[Math.floor(Math.random() * list.length)];
  }

  // ── DOM References ─────────────────────────────────

  var loginOverlay = document.getElementById("login-overlay");
  var loginForm = document.getElementById("login-form");
  var pinInput = document.getElementById("pin-input");
  var loginError = document.getElementById("login-error");
  var appContainer = document.getElementById("app");
  var themeToggle = document.getElementById("theme-toggle");
  var logoutBtn = document.getElementById("logout-btn");
  var tabButtons = document.querySelectorAll(".tab-btn");
  var tabPanels = document.querySelectorAll(".tab-panel");

  // Sidebar elements
  var sidebarVolume = document.getElementById("sidebar-volume");
  var sidebarVolLabel = document.getElementById("sidebar-vol-label");
  var sidebarLed = document.getElementById("sidebar-led");
  var sidebarPuppet = document.getElementById("sidebar-puppet");
  var sidebarSilent = document.getElementById("sidebar-silent");
  var sidebarEndSession = document.getElementById("sidebar-end-session");
  var sidebarStatusDot = document.getElementById("sidebar-status-dot");

  // Header elements
  var headerSubtitle = document.getElementById("header-subtitle");
  var headerStatusDot = document.getElementById("header-status-dot");

  // Footer
  var footerQuote = document.getElementById("footer-quote");

  // FAB + Bottom Sheet
  var fab = document.getElementById("fab");
  var bottomSheetBackdrop = document.getElementById("bottom-sheet-backdrop");
  var bottomSheet = document.getElementById("bottom-sheet");
  var bsVolume = document.getElementById("bs-volume");
  var bsVolLabel = document.getElementById("bs-vol-label");
  var bsLed = document.getElementById("bs-led");
  var bsPuppet = document.getElementById("bs-puppet");
  var bsSilent = document.getElementById("bs-silent");
  var bsEndWrap = document.getElementById("bs-end-wrap");
  var bsEndSession = document.getElementById("bs-end-session");

  // ── Sidebar State ──────────────────────────────────

  var sidebarState = {
    volume: 80,
    ledEnabled: true,
    puppetOnly: false,
    silentWake: false,
    sessionActive: false,
    serviceRunning: false,
  };

  var volumeDebounce = null;
  var _volumeInFlight = false;
  var _volumePending = null;
  var sessionPollInterval = null;
  var servicePollInterval = null;

  // ── API Helpers ────────────────────────────────────

  function api(path, options) {
    var pin = sessionStorage.getItem(PIN_KEY) || "";
    var opts = Object.assign({}, options || {});
    opts.headers = Object.assign({}, opts.headers || {}, {
      "X-Bender-Pin": pin
    });

    return fetch(path, opts).then(function (res) {
      if (res.status === 401) {
        logout();
        return Promise.reject(new Error("Unauthorized"));
      }
      return res;
    });
  }

  function apiJson(path, options) {
    var opts = Object.assign({}, options || {});
    opts.headers = Object.assign({}, opts.headers || {}, {
      "Content-Type": "application/json"
    });
    return api(path, opts).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (body) {
          return Promise.reject(new Error("HTTP " + res.status + ": " + body));
        });
      }
      return res.json();
    });
  }

  function apiDownload(path, filename) {
    return api(path).then(function (res) {
      if (!res.ok) {
        return Promise.reject(new Error("Download failed: HTTP " + res.status));
      }
      return res.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
  }

  // ── DOM Helper ─────────────────────────────────────

  function el(tag, attrs, children) {
    attrs = attrs || {};
    children = children || [];
    var e = document.createElement(tag);
    for (var k in attrs) {
      if (!attrs.hasOwnProperty(k)) continue;
      var v = attrs[k];
      if (k === "className") e.className = v;
      else if (k === "textContent") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2).toLowerCase(), v);
      else e.setAttribute(k, v);
    }
    children.forEach(function (c) {
      if (typeof c === "string") e.appendChild(document.createTextNode(c));
      else if (c) e.appendChild(c);
    });
    return e;
  }

  // Expose helpers globally for tab modules
  window.bender = {
    api: api,
    apiJson: apiJson,
    apiDownload: apiDownload,
    el: el,
    getQuote: getQuote,
    BENDER_QUOTES: BENDER_QUOTES,
    _timerCount: 0,
    _timerFiring: 0
  };

  // ── Login / Logout ─────────────────────────────────

  function showLoginError(msg) {
    loginError.textContent = msg;
    loginError.classList.remove("hidden");
  }

  function hideLoginError() {
    loginError.textContent = "";
    loginError.classList.add("hidden");
  }

  function showApp() {
    loginOverlay.classList.add("hidden");
    appContainer.classList.remove("hidden");
    // Initialise the active tab
    var activeTab = document.querySelector(".tab-btn.active");
    if (activeTab) {
      activateTab(activeTab.getAttribute("data-tab"));
    }
    // Start sidebar init and polling
    initSidebar();
    startSessionPolling();
    startServicePolling();
  }

  function showLogin() {
    loginOverlay.classList.remove("hidden");
    appContainer.classList.add("hidden");
    pinInput.value = "";
    hideLoginError();
    pinInput.focus();
    stopSessionPolling();
    stopServicePolling();
  }

  function logout() {
    sessionStorage.removeItem(PIN_KEY);
    showLogin();
  }

  function attemptLogin(pin) {
    sessionStorage.setItem(PIN_KEY, pin);
    return apiJson("/api/actions/service-status").then(function () {
      showApp();
    }).catch(function () {
      sessionStorage.removeItem(PIN_KEY);
      showLoginError("Invalid PIN. Bite my shiny metal... try again.");
      pinInput.value = "";
      pinInput.focus();
    });
  }

  loginForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var pin = pinInput.value.trim();
    if (!pin) return;
    hideLoginError();
    attemptLogin(pin);
  });

  logoutBtn.addEventListener("click", function () {
    logout();
  });

  // ── Tab Switching ──────────────────────────────────

  var tabInits = {};

  function onTabInit(name, fn) {
    tabInits[name] = fn;
  }

  window.bender.onTabInit = onTabInit;

  function activateTab(tabName) {
    // Update buttons
    tabButtons.forEach(function (btn) {
      var isActive = btn.getAttribute("data-tab") === tabName;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    // Update panels
    tabPanels.forEach(function (panel) {
      var isActive = panel.id === "panel-" + tabName;
      panel.classList.toggle("active", isActive);
    });

    // Call tab init if registered
    if (tabInits[tabName]) {
      tabInits[tabName]();
    }

    // Update footer quote for this tab
    updateFooterQuote(tabName);
  }

  tabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tab = btn.getAttribute("data-tab");
      activateTab(tab);
    });
  });

  // ── Footer Quote ───────────────────────────────────

  function updateFooterQuote(tabName) {
    if (!footerQuote) return;
    footerQuote.textContent = getQuote(tabName || "footer");
  }

  // ── Theme Toggle ───────────────────────────────────

  function getTheme() {
    return localStorage.getItem(THEME_KEY) || "dark";
  }

  function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }

  themeToggle.addEventListener("click", function () {
    var current = document.documentElement.getAttribute("data-theme") || "dark";
    setTheme(current === "dark" ? "light" : "dark");
  });

  // Apply saved theme on load
  setTheme(getTheme());

  // ── Sidebar Init ───────────────────────────────────

  function initSidebar() {
    // Load initial volume
    apiJson("/api/config/volume").then(function (data) {
      if (data.level !== undefined) {
        sidebarState.volume = data.level;
        syncVolumeUI(data.level);
      }
    }).catch(function () {});

    // Load config for LED + silent wake
    apiJson("/api/config").then(function (data) {
      if (data.led_listening_enabled !== undefined) {
        sidebarState.ledEnabled = !!data.led_listening_enabled;
      }
      if (data.silent_wakeword !== undefined) {
        sidebarState.silentWake = !!data.silent_wakeword;
      }
      syncToggleUI();
    }).catch(function () {});
  }

  function syncVolumeUI(level) {
    if (sidebarVolume) {
      sidebarVolume.value = level;
    }
    if (sidebarVolLabel) {
      sidebarVolLabel.textContent = level + "%";
    }
    if (bsVolume) {
      bsVolume.value = level;
    }
    if (bsVolLabel) {
      bsVolLabel.textContent = level + "%";
    }
  }

  function syncToggleUI() {
    // LED
    if (sidebarLed) {
      sidebarLed.classList.toggle("active", sidebarState.ledEnabled);
    }
    if (bsLed) {
      bsLed.classList.toggle("active", sidebarState.ledEnabled);
    }

    // Puppet
    if (sidebarPuppet) {
      sidebarPuppet.classList.toggle("active", sidebarState.puppetOnly);
    }
    if (bsPuppet) {
      bsPuppet.classList.toggle("active", sidebarState.puppetOnly);
    }

    // Silent wake — greyed when LED is off
    var silentDisabled = !sidebarState.ledEnabled;
    if (sidebarSilent) {
      sidebarSilent.classList.toggle("active", sidebarState.silentWake);
      sidebarSilent.classList.toggle("disabled", silentDisabled);
    }
    if (bsSilent) {
      bsSilent.classList.toggle("active", sidebarState.silentWake);
      bsSilent.classList.toggle("disabled", silentDisabled);
    }

    // End session visibility
    var showEnd = sidebarState.sessionActive;
    if (sidebarEndSession) {
      sidebarEndSession.classList.toggle("hidden", !showEnd);
    }
    if (bsEndWrap) {
      bsEndWrap.classList.toggle("hidden", !showEnd);
    }
  }

  // ── Volume Handlers ────────────────────────────────

  function handleVolumeChange(level) {
    // Instant visual feedback — update label immediately
    sidebarState.volume = level;
    syncVolumeUI(level);

    clearTimeout(volumeDebounce);
    volumeDebounce = setTimeout(function () {
      _sendVolume(level);
    }, 100);  // 100ms instead of 300ms
  }

  function _sendVolume(level) {
    if (_volumeInFlight) {
      _volumePending = level;
      return;
    }
    _volumeInFlight = true;
    _volumePending = null;
    apiJson("/api/config/volume", {
      method: "POST",
      body: JSON.stringify({ level: level })
    }).then(function () {
      _volumeInFlight = false;
      if (_volumePending !== null) {
        _sendVolume(_volumePending);
      }
    }).catch(function () {
      _volumeInFlight = false;
      if (_volumePending !== null) {
        _sendVolume(_volumePending);
      }
    });
  }

  if (sidebarVolume) {
    sidebarVolume.addEventListener("input", function () {
      handleVolumeChange(parseInt(sidebarVolume.value, 10));
    });
  }

  if (bsVolume) {
    bsVolume.addEventListener("input", function () {
      handleVolumeChange(parseInt(bsVolume.value, 10));
    });
  }

  // ── Toggle Handlers ────────────────────────────────

  function handleLedToggle() {
    sidebarState.ledEnabled = !sidebarState.ledEnabled;
    syncToggleUI();
    apiJson("/api/config", {
      method: "PUT",
      body: JSON.stringify({ led_listening_enabled: sidebarState.ledEnabled })
    }).catch(function () {
      sidebarState.ledEnabled = !sidebarState.ledEnabled;
      syncToggleUI();
    });
  }

  function handlePuppetToggle() {
    var newMode = sidebarState.puppetOnly ? "converse" : "puppet_only";
    sidebarState.puppetOnly = !sidebarState.puppetOnly;
    syncToggleUI();
    apiJson("/api/actions/toggle-mode", {
      method: "POST",
      body: JSON.stringify({ mode: newMode })
    }).catch(function () {
      sidebarState.puppetOnly = !sidebarState.puppetOnly;
      syncToggleUI();
    });
  }

  function handleSilentToggle() {
    if (!sidebarState.ledEnabled) return;
    sidebarState.silentWake = !sidebarState.silentWake;
    syncToggleUI();
    apiJson("/api/config", {
      method: "PUT",
      body: JSON.stringify({ silent_wakeword: sidebarState.silentWake })
    }).catch(function () {
      sidebarState.silentWake = !sidebarState.silentWake;
      syncToggleUI();
    });
  }

  function handleEndSession() {
    apiJson("/api/actions/end-session", {
      method: "POST",
      body: "{}"
    }).then(function () {
      sidebarState.sessionActive = false;
      syncToggleUI();
    }).catch(function () {});
  }

  // Wire sidebar buttons
  if (sidebarLed) sidebarLed.addEventListener("click", handleLedToggle);
  if (sidebarPuppet) sidebarPuppet.addEventListener("click", handlePuppetToggle);
  if (sidebarSilent) sidebarSilent.addEventListener("click", handleSilentToggle);
  if (sidebarEndSession) sidebarEndSession.addEventListener("click", handleEndSession);

  // Wire bottom-sheet buttons
  if (bsLed) bsLed.addEventListener("click", handleLedToggle);
  if (bsPuppet) bsPuppet.addEventListener("click", handlePuppetToggle);
  if (bsSilent) bsSilent.addEventListener("click", handleSilentToggle);
  if (bsEndSession) bsEndSession.addEventListener("click", handleEndSession);

  // ── Session Polling ────────────────────────────────

  // ── Timer Badge ──────────────────────────────────────
  var timerBadgeEl = null;

  function updateTimerBadge() {
    var count = window.bender._timerCount || 0;
    var firing = window.bender._timerFiring || 0;

    if (!timerBadgeEl) {
      // Create badge next to header subtitle
      timerBadgeEl = el("span", { className: "timer-badge hidden" });
      if (headerSubtitle && headerSubtitle.parentNode) {
        headerSubtitle.parentNode.appendChild(timerBadgeEl);
      }
    }

    if (count === 0) {
      timerBadgeEl.classList.add("hidden");
      timerBadgeEl.classList.remove("firing");
    } else {
      timerBadgeEl.classList.remove("hidden");
      timerBadgeEl.textContent = String(count);
      if (firing > 0) {
        timerBadgeEl.classList.add("firing");
      } else {
        timerBadgeEl.classList.remove("firing");
      }
    }
  }

  function pollSessionStatus() {
    updateTimerBadge();
    apiJson("/api/actions/session-status").then(function (data) {
      var wasActive = sidebarState.sessionActive;
      sidebarState.sessionActive = !!data.active;

      if (wasActive !== sidebarState.sessionActive) {
        syncToggleUI();
      }

      // Update header subtitle
      if (headerSubtitle) {
        if (data.active) {
          headerSubtitle.textContent = "In Conversation";
          if (headerStatusDot) headerStatusDot.className = "header-status-dot in-conversation";
        } else if (sidebarState.serviceRunning) {
          headerSubtitle.textContent = "Bending Unit 22 \u2014 Online";
          if (headerStatusDot) headerStatusDot.className = "header-status-dot";
        } else {
          headerSubtitle.textContent = "Bending Unit 22 \u2014 Offline";
          if (headerStatusDot) headerStatusDot.className = "header-status-dot stopped";
        }
      }
    }).catch(function () {
      // Ignore poll errors silently
    });
  }

  function startSessionPolling() {
    stopSessionPolling();
    pollSessionStatus();
    sessionPollInterval = setInterval(pollSessionStatus, 3000);
  }

  function stopSessionPolling() {
    if (sessionPollInterval) {
      clearInterval(sessionPollInterval);
      sessionPollInterval = null;
    }
  }

  // ── Service Status Polling ─────────────────────────

  function pollServiceStatus() {
    apiJson("/api/actions/service-status").then(function (data) {
      sidebarState.serviceRunning = !!data.running;
      sidebarState.puppetOnly = !data.running;
      syncToggleUI();

      if (sidebarStatusDot) {
        sidebarStatusDot.className = "status-dot " + (data.running ? "running" : "stopped");
      }
    }).catch(function () {
      if (sidebarStatusDot) {
        sidebarStatusDot.className = "status-dot";
      }
    });
  }

  function startServicePolling() {
    stopServicePolling();
    pollServiceStatus();
    servicePollInterval = setInterval(pollServiceStatus, 10000);
  }

  function stopServicePolling() {
    if (servicePollInterval) {
      clearInterval(servicePollInterval);
      servicePollInterval = null;
    }
  }

  // ── FAB + Bottom Sheet ─────────────────────────────

  function openBottomSheet() {
    // Sync bottom sheet UI with current state
    syncVolumeUI(sidebarState.volume);
    syncToggleUI();

    if (bottomSheetBackdrop) bottomSheetBackdrop.classList.add("visible");
    if (bottomSheet) bottomSheet.classList.add("visible");
  }

  function closeBottomSheet() {
    if (bottomSheetBackdrop) bottomSheetBackdrop.classList.remove("visible");
    if (bottomSheet) bottomSheet.classList.remove("visible");
  }

  if (fab) {
    fab.addEventListener("click", function () {
      openBottomSheet();
    });
  }

  if (bottomSheetBackdrop) {
    bottomSheetBackdrop.addEventListener("click", function () {
      closeBottomSheet();
    });
  }

  // ── Auto-Login ─────────────────────────────────────

  var savedPin = sessionStorage.getItem(PIN_KEY);
  if (savedPin) {
    attemptLogin(savedPin);
  } else {
    showLogin();
  }

})();
