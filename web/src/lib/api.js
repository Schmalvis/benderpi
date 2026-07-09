import { get } from 'svelte/store';
import { session } from './stores/session.js';

async function request(path, options = {}) {
  const { token } = get(session);
  const headers = { ...options.headers };
  if (token) headers['X-Bender-Token'] = token;
  if (options.body && typeof options.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    session.logout();
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    // Surface FastAPI's `detail` (e.g. 422 validation errors from the config
    // schema) instead of a bare status line, so the Config editor can tell the
    // user *which* field it rejected rather than just "422 Unprocessable Entity".
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail !== undefined) {
        const d = body.detail;
        if (typeof d === 'string') {
          msg = d;
        } else if (Array.isArray(d)) {
          // pydantic errors: [{loc:[...], msg:'...'}, ...]
          msg = d
            .map((e) => {
              const field = Array.isArray(e.loc) ? e.loc.filter((p) => p !== 'body').join('.') : '';
              return field ? `${field}: ${e.msg}` : e.msg;
            })
            .join('; ');
        }
      }
    } catch { /* non-JSON body — keep the status line */ }
    throw new Error(msg);
  }
  return res.json();
}

// Auth
// login() is intentionally NOT routed through request(): a 401 here means
// "wrong PIN", not "session expired", and we want the server's detail (incl.
// 429 lockout messages) surfaced verbatim to the login form.
export async function login(pin) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pin }),
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') msg = body.detail;
    } catch { /* keep status line */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  const body = await res.json();
  return body.token;
}

export const getStreamToken = () => request('/api/auth/stream-token');

// Health
export const getHealth = () => request('/api/health');
export const getSessionStatus = () => request('/api/actions/session-status');
export const getServiceStatus = () => request('/api/actions/service-status');

// Actions
export const endSession = () => request('/api/actions/end-session', { method: 'POST' });
export const restartService = () => request('/api/actions/restart', { method: 'POST' });
export const refreshBriefings = () => request('/api/actions/refresh-briefings', { method: 'POST' });
export const rebuildResponses = () => request('/api/actions/prebuild', { method: 'POST' });
export const generateStatus = () => request('/api/actions/generate-status', { method: 'POST' });
export const toggleMode = (mode) => request('/api/actions/toggle-mode', { method: 'POST', body: JSON.stringify({ mode }) });

// Config
export const getConfig = () => request('/api/config');
export const updateConfig = (data) => request('/api/config', { method: 'PUT', body: JSON.stringify(data) });
export const getWatchdogConfig = () => request('/api/config/watchdog');
export const updateWatchdogConfig = (data) => request('/api/config/watchdog', { method: 'PUT', body: JSON.stringify(data) });
export const getVolume = () => request('/api/config/volume');
export const setVolume = (volume) => request('/api/config/volume', { method: 'POST', body: JSON.stringify({ volume }) });

// Puppet
export const speak = (text) => request('/api/puppet/speak', { method: 'POST', body: JSON.stringify({ text }) });
export const playClip = (path) => request('/api/puppet/clip', { method: 'POST', body: JSON.stringify({ path }) });
export const getClips = () => request('/api/puppet/clips');
export const setFavourite = (path, favourite) => request('/api/puppet/favourite', { method: 'POST', body: JSON.stringify({ path, favourite }) });
export const getCameraStatus = () => request('/api/puppet/camera/status');
// Callers fetch a short-lived stream token first, then embed it in the URL.
export const cameraStreamUrl = (streamToken) => `/api/puppet/camera/stream?token=${encodeURIComponent(streamToken)}`;

// Status
export const getStatus = () => request('/api/status');
export const refreshStatus = () => request('/api/status/refresh', { method: 'POST' });

// Logs
export const getConversationDates = () => request('/api/logs/conversations');
export const getConversationLog = (date) => request(`/api/logs/conversations/${date}`);
export const getSystemLog = (lines = 100) => request(`/api/logs/system?lines=${lines}`);
export const getMetrics = () => request('/api/logs/metrics');
export const downloadLog = (filename) => `/api/logs/download/${filename}`;

// Timers
export const getTimers = () => request('/api/timers');
export const createTimer = (data) => request('/api/timers', { method: 'POST', body: JSON.stringify(data) });
export const cancelTimer = (id) => request(`/api/timers/${id}`, { method: 'DELETE' });
export const dismissTimer = (id) => request(`/api/timers/${id}/dismiss`, { method: 'POST' });
export const dismissAllTimers = () => request('/api/timers/dismiss-all', { method: 'POST' });

// Remote
export const remoteAsk = (text) => request('/api/remote/ask-text', { method: 'POST', body: JSON.stringify({ text }) });

// Vision
export const getVisionPassive = () => request('/api/vision/passive');
export const enableVisionPassive = (duration_minutes) => request('/api/vision/passive', { method: 'POST', body: JSON.stringify({ duration_minutes }) });
export const disableVisionPassive = () => request('/api/vision/passive', { method: 'DELETE' });
export const analyseVision = () => request('/api/vision/analyse', { method: 'POST' });
