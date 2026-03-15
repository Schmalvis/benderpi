/* ═══════════════════════════════════════════════════════
   BenderPi Web UI — Core Application
   Login, tabs, theme toggle, API helpers
   ═══════════════════════════════════════════════════════ */

(function () {
  "use strict";

  // ── Constants ──────────────────────────────────────

  var PIN_KEY = "benderpi_pin";
  var THEME_KEY = "benderpi_theme";

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

  // ── API Helpers ────────────────────────────────────

  /**
   * Fetch wrapper that injects the PIN header and handles 401.
   * SECURITY: Never passes untrusted content to innerHTML.
   * @param {string} path - API path (e.g. "/api/health")
   * @param {object} [options] - fetch options
   * @returns {Promise<Response>}
   */
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

  /**
   * Fetch JSON from API.
   * @param {string} path - API path
   * @param {object} [options] - fetch options
   * @returns {Promise<object>}
   */
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

  /**
   * Download a file from the API with PIN auth.
   * @param {string} path - API path
   * @param {string} filename - suggested filename for download
   */
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

  /**
   * Create a DOM element with attributes and children.
   * Safe: never uses innerHTML. All text goes through textContent/createTextNode.
   * @param {string} tag - element tag name
   * @param {object} [attrs] - attributes/properties (className, textContent, onXxx, etc.)
   * @param {Array} [children] - child elements or strings
   * @returns {HTMLElement}
   */
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
    el: el
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
  }

  function showLogin() {
    loginOverlay.classList.remove("hidden");
    appContainer.classList.add("hidden");
    pinInput.value = "";
    hideLoginError();
    pinInput.focus();
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

  /** Tab init functions — populated by tab modules */
  var tabInits = {};

  /**
   * Register a tab init function.
   * @param {string} name - tab name (puppet, dashboard, logs, config)
   * @param {function} fn - called when tab is first activated or re-activated
   */
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
  }

  tabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tab = btn.getAttribute("data-tab");
      activateTab(tab);
    });
  });

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

  // ── Auto-Login ─────────────────────────────────────

  var savedPin = sessionStorage.getItem(PIN_KEY);
  if (savedPin) {
    attemptLogin(savedPin);
  } else {
    showLogin();
  }

})();
