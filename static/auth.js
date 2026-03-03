/**
 * GenBI Auth – shared fetch helpers + form handlers.
 */

const AUTH_TOKEN_KEY = 'genbi-auth-token';
const AUTH_USER_KEY = 'genbi-auth-user';

// ── Token management ────────────────────────────────────────────────────────

function saveAuth(token, user) {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
}

function getToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

function getUser() {
  try { return JSON.parse(localStorage.getItem(AUTH_USER_KEY)); } catch { return null; }
}

function clearAuth() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
}

function isLoggedIn() {
  return !!getToken();
}

// ── CSRF token ──────────────────────────────────────────────────────────────

let _csrfToken = null;

async function getCsrfToken() {
  if (_csrfToken) return _csrfToken;
  try {
    const res = await fetch('/api/csrf-token', { credentials: 'same-origin' });
    const data = await res.json();
    _csrfToken = data.csrf_token || '';
  } catch {
    _csrfToken = '';
  }
  return _csrfToken;
}

// ── API helper ──────────────────────────────────────────────────────────────

async function authFetch(url, body) {
  const csrf = await getCsrfToken();
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf,
    },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const data = await res.json();
  return { ok: res.ok, status: res.status, data };
}

// ── UI helpers ──────────────────────────────────────────────────────────────

function showAlert(id, message, type) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'auth-alert ' + type;
  el.textContent = message;
  el.style.display = 'block';
}

function hideAlert(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

function setLoading(btn, loading) {
  if (loading) {
    btn.classList.add('loading');
    btn.disabled = true;
  } else {
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

// ── Password visibility toggle ──────────────────────────────────────────────

function initPasswordToggles() {
  document.querySelectorAll('.password-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = btn.parentElement.querySelector('input');
      const isPassword = input.type === 'password';
      input.type = isPassword ? 'text' : 'password';
      btn.innerHTML = isPassword
        ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
        : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    });
  });
}

// ── Password strength meter ─────────────────────────────────────────────────

function initStrengthMeter(inputId, meterId, textId) {
  const input = document.getElementById(inputId);
  const meter = document.getElementById(meterId);
  const text = document.getElementById(textId);
  if (!input || !meter) return;

  input.addEventListener('input', () => {
    const val = input.value;
    let score = 0;
    if (val.length >= 8) score++;
    if (val.length >= 12) score++;
    if (/[A-Z]/.test(val) && /[a-z]/.test(val)) score++;
    if (/[0-9]/.test(val)) score++;
    if (/[^A-Za-z0-9]/.test(val)) score++;

    const pct = Math.min(100, (score / 5) * 100);
    const colors = ['hsl(350 80% 55%)', 'hsl(30 90% 55%)', 'hsl(45 90% 55%)', 'hsl(120 50% 50%)', 'hsl(150 60% 45%)'];
    const labels = ['Very weak', 'Weak', 'Fair', 'Good', 'Strong'];

    meter.style.width = pct + '%';
    meter.style.background = colors[Math.max(0, score - 1)] || colors[0];
    if (text) {
      text.textContent = val.length > 0 ? labels[Math.max(0, score - 1)] || '' : '';
      text.style.color = colors[Math.max(0, score - 1)] || '';
    }
  });
}

// ── Google OAuth ────────────────────────────────────────────────────────────

/**
 * Reads GOOGLE_OAUTH_CLIENT_ID from a meta tag or falls back to window config.
 * The server can inject this via template or we read from /api/auth/config.
 */
function _getGoogleClientId() {
  const meta = document.querySelector('meta[name="google-client-id"]');
  if (meta) return meta.content;
  return window.__GENBI_GOOGLE_CLIENT_ID || '';
}

function initGoogleOAuth() {
  const clientId = _getGoogleClientId();
  if (!clientId) {
    showAlert('alert', 'Google Sign-In is not configured.', 'error');
    return;
  }

  const redirectUri = window.location.origin + '/auth/google/callback';
  const scope = 'openid email profile';
  const url = 'https://accounts.google.com/o/oauth2/v2/auth'
    + '?client_id=' + encodeURIComponent(clientId)
    + '&redirect_uri=' + encodeURIComponent(redirectUri)
    + '&response_type=code'
    + '&scope=' + encodeURIComponent(scope)
    + '&access_type=offline'
    + '&prompt=consent';

  window.location.href = url;
}

async function handleGoogleCallback() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const error = params.get('error');

  if (error) {
    window.location.href = '/login';
    return;
  }
  if (!code) return;

  const redirectUri = window.location.origin + '/auth/google/callback';

  try {
    const csrf = await getCsrfToken();
    const res = await fetch('/api/auth/google', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      credentials: 'same-origin',
      body: JSON.stringify({ code, redirect_uri: redirectUri }),
    });
    const data = await res.json();

    if (res.ok && data.token) {
      saveAuth(data.token, data.user);
      if (data.email_verification_required) {
        window.location.href = '/verify-email';
      } else {
        window.location.href = '/dashboard';
      }
    } else {
      // Redirect to login with error
      window.location.href = '/login?error=' + encodeURIComponent(data.error || 'Google login failed');
    }
  } catch {
    window.location.href = '/login?error=Google+login+failed';
  }
}

// ── Init on load ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initPasswordToggles();

  // Show error from query param (e.g. after failed Google callback)
  const urlError = new URLSearchParams(window.location.search).get('error');
  if (urlError) {
    showAlert('alert', urlError, 'error');
  }
});
