/* ═══════════════════════════════════════════════════════
   BenderPi Web UI — Puppet Mode
   TTS speak with speech rate, soundboard with favourites
   ═══════════════════════════════════════════════════════ */

(function () {
  "use strict";

  var api = window.bender.api;
  var apiJson = window.bender.apiJson;
  var el = window.bender.el;

  // ── State ────────────────────────────────────────────

  var clips = [];
  var favouritesSet = {};
  var panel = null;
  var favsContainer = null;
  var clipsContainer = null;
  var initialised = false;

  // Speech rate
  var speechRateSlider = null;
  var speechRateLabel = null;
  var speechRateDebounce = null;

  // ── Init ─────────────────────────────────────────────

  function initPuppet() {
    panel = document.getElementById("panel-puppet");
    if (!panel) return;

    if (!initialised) {
      panel.textContent = "";
      buildUI();
      initialised = true;
    }

    loadSpeechRate();
    loadClips();
  }

  function buildUI() {
    // Speak section (with speech rate)
    var speakSection = buildSpeakSection();
    panel.appendChild(speakSection);

    // Favourites section
    var favsSection = el("div", { className: "card puppet-section" }, [
      el("div", { className: "card-header" }, [
        el("h3", { textContent: "Favourites" })
      ])
    ]);
    favsContainer = el("div", { className: "puppet-favs" });
    favsSection.appendChild(favsContainer);
    panel.appendChild(favsSection);

    // All clips section
    var clipsSection = el("div", { className: "card puppet-section" }, [
      el("div", { className: "card-header" }, [
        el("h3", { textContent: "All Clips" })
      ])
    ]);
    clipsContainer = el("div", { className: "puppet-clips-list" });
    clipsSection.appendChild(clipsContainer);
    panel.appendChild(clipsSection);
  }

  // ── Speech Rate ────────────────────────────────────────

  function loadSpeechRate() {
    apiJson("/api/config").then(function (data) {
      if (speechRateSlider && data.speech_rate !== undefined) {
        speechRateSlider.value = data.speech_rate;
        speechRateLabel.textContent = "Speed: " + Number(data.speech_rate).toFixed(1) + "x";
      }
    }).catch(function () {
      // Config read failed — leave default
    });
  }

  function setSpeechRate(value) {
    clearTimeout(speechRateDebounce);
    speechRateDebounce = setTimeout(function () {
      apiJson("/api/config", {
        method: "PUT",
        body: JSON.stringify({ speech_rate: value })
      }).catch(function () {
        // Silently fail
      });
    }, 300);
  }

  // ── Speak ────────────────────────────────────────────

  function buildSpeakSection() {
    var section = el("div", { className: "card puppet-section" });

    var header = el("div", { className: "card-header" }, [
      el("h3", { textContent: "Speak" })
    ]);
    section.appendChild(header);

    var textarea = el("textarea", {
      className: "puppet-speak-input",
      placeholder: "Type something for Bender to say...",
      rows: "3",
      maxlength: "500"
    });

    var counter = el("span", {
      className: "puppet-char-counter text-muted",
      textContent: "0 / 500"
    });

    var statusText = el("span", {
      className: "puppet-speak-status",
      textContent: ""
    });

    var speakBtn = el("button", {
      className: "btn btn-primary",
      textContent: "SPEAK",
      onClick: function () {
        var text = textarea.value.trim();
        if (!text) return;
        speakBtn.disabled = true;
        statusText.textContent = "Speaking...";
        statusText.className = "puppet-speak-status";

        apiJson("/api/puppet/speak", {
          method: "POST",
          body: JSON.stringify({ text: text })
        }).then(function () {
          statusText.textContent = "Done";
          statusText.className = "puppet-speak-status success";
          speakBtn.disabled = false;
        }).catch(function (err) {
          statusText.textContent = "Error: " + err.message;
          statusText.className = "puppet-speak-status error";
          speakBtn.disabled = false;
        });
      }
    });

    textarea.addEventListener("input", function () {
      counter.textContent = textarea.value.length + " / 500";
    });

    // Speech rate slider row
    var rateRow = el("div", { className: "puppet-volume-row" });

    speechRateSlider = el("input", {
      type: "range",
      min: "0.5",
      max: "2.0",
      step: "0.1",
      value: "1.0",
      className: "puppet-volume-slider",
      onInput: function () {
        var val = parseFloat(speechRateSlider.value);
        speechRateLabel.textContent = "Speed: " + val.toFixed(1) + "x";
        setSpeechRate(val);
      }
    });

    speechRateLabel = el("span", {
      className: "puppet-volume-label mono",
      textContent: "Speed: 1.0x"
    });

    rateRow.appendChild(speechRateSlider);
    rateRow.appendChild(speechRateLabel);

    var controls = el("div", { className: "puppet-speak-controls" }, [
      counter,
      statusText,
      speakBtn
    ]);

    section.appendChild(textarea);
    section.appendChild(rateRow);
    section.appendChild(controls);
    return section;
  }

  // ── Clips ────────────────────────────────────────────

  function loadClips() {
    apiJson("/api/puppet/clips").then(function (data) {
      clips = data.clips || [];
      favouritesSet = {};
      clips.forEach(function (c) {
        if (c.favourite) favouritesSet[c.path] = true;
      });
      renderFavourites();
      renderClips();
    }).catch(function () {
      // Failed to load clips
      if (clipsContainer) {
        clipsContainer.textContent = "";
        clipsContainer.appendChild(
          el("p", { className: "text-muted", textContent: "Could not load clips." })
        );
      }
    });
  }

  function renderFavourites() {
    if (!favsContainer) return;
    favsContainer.textContent = "";

    var favClips = clips.filter(function (c) { return favouritesSet[c.path]; });

    if (favClips.length === 0) {
      favsContainer.appendChild(
        el("p", { className: "text-muted puppet-empty", textContent: "No favourites yet. Star a clip below to pin it here." })
      );
      return;
    }

    var scrollRow = el("div", { className: "puppet-favs-scroll" });

    favClips.forEach(function (clip) {
      var favLabel = clip.label || clip.name;
      var favDisplayLabel = favLabel.length > 40 ? favLabel.substring(0, 37) + "..." : favLabel;
      var pill = el("button", {
        className: "btn btn-sm puppet-fav-pill",
        title: favLabel,
        onClick: function () { playClip(clip.path, pill); }
      }, [
        el("span", { textContent: favDisplayLabel }),
        el("span", { className: "puppet-play-icon", textContent: "\u25B6" })
      ]);

      var unstar = el("button", {
        className: "btn btn-icon btn-sm puppet-unstar",
        title: "Remove from favourites",
        textContent: "\u2605",
        onClick: function () { toggleFavourite(clip.path, false); }
      });

      var wrapper = el("div", { className: "puppet-fav-item" }, [pill, unstar]);
      scrollRow.appendChild(wrapper);
    });

    favsContainer.appendChild(scrollRow);
  }

  function renderClips() {
    if (!clipsContainer) return;
    clipsContainer.textContent = "";

    // Group by category
    var categories = {};
    clips.forEach(function (c) {
      var cat = c.category || "uncategorised";
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(c);
    });

    var catNames = Object.keys(categories).sort();

    if (catNames.length === 0) {
      clipsContainer.appendChild(
        el("p", { className: "text-muted puppet-empty", textContent: "No clips found." })
      );
      return;
    }

    catNames.forEach(function (cat) {
      var catClips = categories[cat];
      var details = el("details", { className: "puppet-cat-details" });

      var summary = el("summary", {}, [
        document.createTextNode(cat),
        el("span", {
          className: "badge badge-muted puppet-cat-count",
          textContent: String(catClips.length)
        })
      ]);
      details.appendChild(summary);

      var grid = el("div", { className: "puppet-clip-grid" });

      catClips.forEach(function (clip) {
        var isFav = !!favouritesSet[clip.path];

        var clipLabel = clip.label || clip.name;
        var displayLabel = clipLabel.length > 40 ? clipLabel.substring(0, 37) + "..." : clipLabel;
        var playBtn = el("button", {
          className: "btn btn-sm puppet-clip-play",
          title: clipLabel,
          onClick: function () { playClip(clip.path, playBtn); }
        }, [
          el("span", { className: "puppet-play-icon", textContent: "\u25B6" }),
          el("span", { textContent: displayLabel })
        ]);

        var starBtn = el("button", {
          className: "btn btn-icon btn-sm puppet-star" + (isFav ? " puppet-star-active" : ""),
          title: isFav ? "Remove from favourites" : "Add to favourites",
          textContent: isFav ? "\u2605" : "\u2606",
          onClick: function () { toggleFavourite(clip.path, !favouritesSet[clip.path]); }
        });

        var item = el("div", { className: "puppet-clip-item" }, [playBtn, starBtn]);
        grid.appendChild(item);
      });

      details.appendChild(grid);
      clipsContainer.appendChild(details);
    });
  }

  // ── Actions ──────────────────────────────────────────

  function playClip(path, btn) {
    if (btn) btn.disabled = true;
    apiJson("/api/puppet/clip", {
      method: "POST",
      body: JSON.stringify({ path: path })
    }).then(function () {
      if (btn) btn.disabled = false;
    }).catch(function () {
      if (btn) btn.disabled = false;
    });
  }

  function toggleFavourite(path, favourite) {
    apiJson("/api/puppet/favourite", {
      method: "POST",
      body: JSON.stringify({ path: path, favourite: favourite })
    }).then(function () {
      if (favourite) {
        favouritesSet[path] = true;
      } else {
        delete favouritesSet[path];
      }
      // Update clip objects
      clips.forEach(function (c) {
        if (c.path === path) c.favourite = favourite;
      });
      renderFavourites();
      renderClips();
    }).catch(function () {
      // Silently fail
    });
  }

  // ── Register ─────────────────────────────────────────

  window.bender.onTabInit("puppet", initPuppet);

})();
