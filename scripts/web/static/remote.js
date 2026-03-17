/**
 * remote.js — Remote voice interaction for BenderPi
 *
 * One button, two modes:
 *   Hold-to-talk : press & hold → release sends
 *   Tap-to-toggle: quick tap → records until second tap or 10 s auto-stop
 */
(function () {
  'use strict';

  const HOLD_THRESHOLD_MS  = 300;
  const MAX_RECORD_S       = 10;
  const RING_R             = 54;
  const RING_CIRC          = 2 * Math.PI * RING_R;

  // ── State ──────────────────────────────────────────────────────────────────
  let state          = 'IDLE'; // IDLE | PRESS_DETECT | HOLD_REC | TAP_REC | PROCESSING | PLAYING
  let mediaRecorder  = null;
  let audioStream    = null;
  let audioChunks    = [];
  let holdTimer      = null;
  let countdownTimer = null;
  let cdRemaining    = MAX_RECORD_S;
  let pressTime      = 0;

  // ── DOM refs ───────────────────────────────────────────────────────────────
  let btnEl, ringProgress, hintEl, statusEl, historyEl, micErrEl, countdownEl, micIconEl, spinnerEl;

  // ── Init ───────────────────────────────────────────────────────────────────
  function initRemote() {
    const panel = document.getElementById('panel-remote');
    if (!panel || document.getElementById('remote-btn')) return;

    panel.innerHTML = `
      <div class="remote-container">
        <div class="remote-btn-wrap">
          <svg class="remote-ring" viewBox="0 0 120 120" aria-hidden="true">
            <circle class="remote-ring-bg" cx="60" cy="60" r="${RING_R}"/>
            <circle class="remote-ring-prog" cx="60" cy="60" r="${RING_R}"
                    stroke-dasharray="${RING_CIRC.toFixed(1)}"
                    stroke-dashoffset="${RING_CIRC.toFixed(1)}"
                    transform="rotate(-90 60 60)"/>
          </svg>
          <button id="remote-btn" class="remote-btn" aria-label="Talk to Bender" touch-action="none">
            <svg id="remote-mic" class="remote-btn-icon" viewBox="0 0 24 24"
                 fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="2" width="6" height="13" rx="3"/>
              <path d="M5 10a7 7 0 0 0 14 0"/>
              <line x1="12" y1="19" x2="12" y2="23"/>
              <line x1="8"  y1="23" x2="16" y2="23"/>
            </svg>
            <svg id="remote-spin" class="remote-btn-icon remote-spinner hidden" viewBox="0 0 24 24"
                 fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <path d="M12 2a10 10 0 0 1 10 10"/>
            </svg>
            <span id="remote-cd" class="remote-countdown hidden"></span>
          </button>
        </div>

        <p id="remote-hint"   class="remote-hint">Hold to talk &nbsp;•&nbsp; Tap to toggle</p>
        <p id="remote-status" class="remote-status hidden"></p>

        <div id="remote-mic-err" class="remote-mic-err hidden">
          <p id="remote-mic-err-msg">Microphone access denied.</p>
          <p>Allow microphone access in your browser settings, then reload.</p>
        </div>

        <div id="remote-history" class="remote-history"></div>
      </div>`;

    btnEl        = document.getElementById('remote-btn');
    ringProgress = panel.querySelector('.remote-ring-prog');
    hintEl       = document.getElementById('remote-hint');
    statusEl     = document.getElementById('remote-status');
    historyEl    = document.getElementById('remote-history');
    micErrEl     = document.getElementById('remote-mic-err');
    countdownEl  = document.getElementById('remote-cd');
    micIconEl    = document.getElementById('remote-mic');
    spinnerEl    = document.getElementById('remote-spin');

    btnEl.addEventListener('pointerdown',   onDown);
    btnEl.addEventListener('pointerup',     onUp);
    btnEl.addEventListener('pointercancel', onCancel);
    btnEl.addEventListener('contextmenu', e => e.preventDefault());
  }

  // ── Pointer events ─────────────────────────────────────────────────────────
  function onDown(e) {
    e.preventDefault();
    if (state !== 'IDLE') {
      // Second tap while TAP_REC → send
      if (state === 'TAP_REC') { stopAndSend(); }
      return;
    }
    pressTime = Date.now();
    startRecording().then(ok => {
      if (!ok) return;
      state = 'PRESS_DETECT';
      holdTimer = setTimeout(() => {
        state = 'HOLD_REC';
        btnEl.classList.add('recording');
        setHint('Release to send');
      }, HOLD_THRESHOLD_MS);
    });
  }

  function onUp(e) {
    e.preventDefault();
    if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }

    if (state === 'PRESS_DETECT') {
      // Quick tap → toggle mode
      state = 'TAP_REC';
      btnEl.classList.add('recording');
      setHint('Tap again to send &nbsp;•&nbsp; or wait');
      startCountdown();
    } else if (state === 'HOLD_REC') {
      stopAndSend();
    }
    // TAP_REC second-tap handled in onDown
  }

  function onCancel() {
    if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
    if (state === 'HOLD_REC' || state === 'TAP_REC') {
      stopAndSend();
    } else {
      stopRecording().then(() => { state = 'IDLE'; });
    }
  }

  // ── Recording ──────────────────────────────────────────────────────────────
  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showMicErr('getUserMedia not available — ensure you are using HTTPS.');
      return false;
    }
    try {
      audioStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (err) {
      const msg = err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'
        ? 'Microphone access denied.'
        : 'Microphone error: ' + err.message;
      showMicErr(msg);
      return false;
    }
    audioChunks = [];
    const mime = bestMime();
    mediaRecorder = new MediaRecorder(audioStream, mime ? { mimeType: mime } : {});
    mediaRecorder.ondataavailable = e => { if (e.data && e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.start(100);
    return true;
  }

  function stopRecording() {
    return new Promise(resolve => {
      if (!mediaRecorder || mediaRecorder.state === 'inactive') { resolve(null); return; }
      mediaRecorder.onstop = () => {
        const mime = mediaRecorder.mimeType || 'audio/webm';
        const blob = new Blob(audioChunks, { type: mime });
        if (audioStream) { audioStream.getTracks().forEach(t => t.stop()); audioStream = null; }
        resolve(blob);
      };
      mediaRecorder.stop();
    });
  }

  // ── Countdown ring ─────────────────────────────────────────────────────────
  function startCountdown() {
    cdRemaining = MAX_RECORD_S;
    setRing(MAX_RECORD_S);
    showCd(MAX_RECORD_S);
    countdownTimer = setInterval(() => {
      cdRemaining--;
      setRing(cdRemaining);
      showCd(cdRemaining);
      if (cdRemaining <= 0) { clearInterval(countdownTimer); countdownTimer = null; stopAndSend(); }
    }, 1000);
  }

  function stopCountdown() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    setRing(0);
    hideCd();
  }

  function setRing(remaining) {
    if (!ringProgress) return;
    const offset = RING_CIRC * (1 - Math.max(0, remaining) / MAX_RECORD_S);
    ringProgress.style.strokeDashoffset = offset.toFixed(1);
  }

  function showCd(n) {
    if (!countdownEl) return;
    countdownEl.textContent = n;
    countdownEl.classList.remove('hidden');
    micIconEl && micIconEl.classList.add('hidden');
  }

  function hideCd() {
    countdownEl && countdownEl.classList.add('hidden');
    micIconEl  && micIconEl.classList.remove('hidden');
  }

  // ── Send ───────────────────────────────────────────────────────────────────
  async function stopAndSend() {
    if (state === 'IDLE' || state === 'PROCESSING' || state === 'PLAYING') return;
    stopCountdown();
    btnEl.classList.remove('recording');
    state = 'PROCESSING';
    setHint('');
    setProcessing(true);

    const blob = await stopRecording();
    if (!blob || blob.size < 500) {
      showStatus('Nothing recorded — try again', true);
      reset(); return;
    }

    try {
      const form = new FormData();
      form.append('audio', blob, 'rec' + extFromMime(blob.type));

      const fetchFn = (window.bender && window.bender.api) ? window.bender.api.bind(window.bender) : fetch;
      const resp = await fetchFn('/api/remote/ask', { method: 'POST', body: form });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Server error ' + resp.status);
      }

      const data = await resp.json();
      setProcessing(false);
      state = 'PLAYING';
      addHistory(data.transcript, data.response_text, data.intent, data.duration_ms);
      await playB64(data.audio_b64);
    } catch (err) {
      showStatus('Error: ' + err.message, true);
    } finally {
      reset();
    }
  }

  // ── Playback ───────────────────────────────────────────────────────────────
  function playB64(b64) {
    return new Promise((resolve, reject) => {
      try {
        const bytes  = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        const blob   = new Blob([bytes], { type: 'audio/wav' });
        const url    = URL.createObjectURL(blob);
        const player = new Audio(url);
        showStatus('Playing…', false);
        player.onended = () => { URL.revokeObjectURL(url); resolve(); };
        player.onerror = e  => { URL.revokeObjectURL(url); reject(new Error('Playback failed')); };
        player.play().catch(reject);
      } catch (e) { reject(e); }
    });
  }

  // ── History ────────────────────────────────────────────────────────────────
  function addHistory(transcript, responseText, intent, durationMs) {
    if (!historyEl) return;
    const youText    = transcript || '(silence)';
    const benderText = formatResponse(responseText, intent);
    const ms         = durationMs ? ` <span class="remote-hist-ms">${durationMs} ms</span>` : '';

    const el = document.createElement('div');
    el.className = 'remote-hist-item';
    el.innerHTML =
      `<div class="remote-hist-you"><span class="remote-hist-label">You</span>${esc(youText)}</div>` +
      `<div class="remote-hist-bender"><span class="remote-hist-label">Bender</span>${esc(benderText)}${ms}</div>`;
    historyEl.insertBefore(el, historyEl.firstChild);
    while (historyEl.children.length > 5) historyEl.removeChild(historyEl.lastChild);
  }

  function formatResponse(text, intent) {
    if (!text) return '(audio played)';
    if (intent === 'WEATHER')    return 'Weather briefing';
    if (intent === 'NEWS')       return 'News briefing';
    if (intent === 'HA_CONTROL') return 'Home Assistant command';
    if (intent === 'SILENCE')    return text;
    if (text.endsWith('.wav'))   return text.replace(/_/g, ' ').replace(/\.wav$/, '');
    return text.length > 120 ? text.slice(0, 117) + '…' : text;
  }

  // ── UI helpers ─────────────────────────────────────────────────────────────
  function setHint(html) {
    if (!hintEl) return;
    if (html) { hintEl.innerHTML = html; hintEl.classList.remove('hidden'); }
    else { hintEl.classList.add('hidden'); }
  }

  function showStatus(text, isError) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = 'remote-status' + (isError ? ' is-error' : '');
    statusEl.classList.remove('hidden');
  }

  function setProcessing(on) {
    if (!btnEl) return;
    if (on) {
      spinnerEl && spinnerEl.classList.remove('hidden');
      micIconEl && micIconEl.classList.add('hidden');
      btnEl.classList.add('processing');
      showStatus('Bender is thinking…', false);
    } else {
      spinnerEl && spinnerEl.classList.add('hidden');
      micIconEl && micIconEl.classList.remove('hidden');
      btnEl.classList.remove('processing');
      statusEl && statusEl.classList.add('hidden');
    }
  }

  function showMicErr(msg) {
    if (!micErrEl) return;
    const p = document.getElementById('remote-mic-err-msg');
    if (p && msg) p.textContent = msg;
    micErrEl.classList.remove('hidden');
    state = 'IDLE';
  }

  function reset() {
    state = 'IDLE';
    btnEl && btnEl.classList.remove('recording', 'processing');
    stopCountdown();
    hideCd();
    setHint('Hold to talk &nbsp;•&nbsp; Tap to toggle');
    setTimeout(() => { statusEl && statusEl.classList.add('hidden'); }, 3000);
  }

  // ── Utilities ──────────────────────────────────────────────────────────────
  function bestMime() {
    const candidates = [
      'audio/webm;codecs=opus', 'audio/webm',
      'audio/ogg;codecs=opus',  'audio/mp4',
    ];
    return candidates.find(t => MediaRecorder.isTypeSupported(t)) || '';
  }

  function extFromMime(mime) {
    if (mime.includes('mp4'))  return '.mp4';
    if (mime.includes('ogg'))  return '.ogg';
    return '.webm';
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', initRemote);

})();
