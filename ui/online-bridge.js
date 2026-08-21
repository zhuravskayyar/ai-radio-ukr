(() => {
  if (window.pywebview?.api) return;

  const backendKey = 'vector-radio-online-backend';
  const tokenKey = 'vector-radio-online-token';
  let apiBase = '';
  let token = '';
  let bridgeInstalled = false;

  function normalizeBackend(value) {
    const candidate = String(value || '').trim();
    if (!candidate) return '';
    let url;
    try {
      url = new URL(candidate);
    } catch (_error) {
      throw new Error('Вкажіть повну адресу, наприклад https://radio.example.com');
    }
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) {
      throw new Error('Для віддаленого сервера потрібна адреса HTTPS');
    }
    return url.origin;
  }

  function configuredBackend() {
    const query = new URLSearchParams(location.search).get('server');
    if (query) return query;
    const configured = window.VECTOR_RADIO_ONLINE_CONFIG?.apiBase;
    if (configured) return configured;
    try {
      return localStorage.getItem(backendKey) || '';
    } catch (_error) {
      return '';
    }
  }

  function readToken() {
    try {
      const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
      token = hash.get('token') || sessionStorage.getItem(tokenKey) || '';
      if (hash.has('token')) {
        history.replaceState(null, '', `${location.pathname}${location.search}`);
      }
    } catch (_error) {
      token = '';
    }
  }

  async function healthCheck(base) {
    const response = await fetch(`${base}/api/health`, {
      cache: 'no-store',
      credentials: 'include',
    });
    if (!response.ok) throw new Error(`Сервер відповів HTTP ${response.status}`);
    const health = await response.json();
    if (!health?.online) throw new Error('Це не Vector Radio Server');
    return health;
  }

  function connectionPanel() {
    return document.querySelector('#onlineConnection');
  }

  function showConnection(message = '') {
    const reveal = () => {
      const panel = connectionPanel();
      if (!panel) return;
      panel.hidden = false;
      document.body.dataset.connectionRequired = '1';
      const status = panel.querySelector('#onlineConnectionStatus');
      if (status) status.textContent = message;
      const input = panel.querySelector('#onlineServerUrl');
      if (input && !input.value) input.value = configuredBackend();
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', reveal, {once: true});
    } else {
      reveal();
    }
  }

  function hideConnection() {
    const panel = connectionPanel();
    if (panel) panel.hidden = true;
    delete document.body.dataset.connectionRequired;
  }

  async function rpc(method, args, retry = true) {
    const headers = {'Content-Type': 'application/json'};
    if (token) headers['X-Vector-Radio-Token'] = token;
    const response = await fetch(`${apiBase}/api/rpc/${encodeURIComponent(method)}`, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers,
      body: JSON.stringify({args}),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {ok: false, error: `HTTP ${response.status}`};
    }
    if (response.status === 401 && retry) {
      const entered = window.prompt('Введіть токен адміністратора Vector Radio:', '');
      if (!entered) throw new Error(payload?.error || 'Авторизацію скасовано');
      token = entered.trim();
      try { sessionStorage.setItem(tokenKey, token); } catch (_error) {}
      return rpc(method, args, false);
    }
    if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
    if (token && apiBase === location.origin) {
      token = '';
      try { sessionStorage.removeItem(tokenKey); } catch (_error) {}
    }
    if (method === 'bootstrap' && payload?.online?.role) {
      document.body.dataset.onlineRole = payload.online.role;
      document.body.dataset.onlineMode = '1';
      document.dispatchEvent(new CustomEvent('vectorradioonline', {detail: payload.online}));
    }
    return payload;
  }

  function installBridge(base) {
    if (bridgeInstalled) return;
    bridgeInstalled = true;
    apiBase = base;
    window.VECTOR_RADIO_API_BASE = apiBase;
    window.pywebview = {
      api: new Proxy({}, {
        get(_target, method) {
          if (typeof method !== 'string') return undefined;
          return (...args) => rpc(method, args);
        },
      }),
    };
    hideConnection();
    window.dispatchEvent(new Event('pywebviewready'));
  }

  async function connect(value, remember = true) {
    const base = normalizeBackend(value);
    const panel = connectionPanel();
    const status = panel?.querySelector('#onlineConnectionStatus');
    if (status) status.textContent = 'Перевіряю Vector Radio Server…';
    await healthCheck(base);
    if (remember) {
      try { localStorage.setItem(backendKey, base); } catch (_error) {}
    }
    installBridge(base);
  }

  window.VectorRadioOnline = {connect};
  readToken();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('service-worker.js', {scope: './'})
      .catch(error => console.warn('PWA registration failed', error));
  }

  const bindConnectionForm = () => {
    const form = document.querySelector('#onlineConnectionForm');
    if (!form || form.dataset.bound) return;
    form.dataset.bound = '1';
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const input = form.querySelector('#onlineServerUrl');
      try {
        await connect(input?.value || '');
      } catch (error) {
        showConnection(error?.message || String(error));
      }
    });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindConnectionForm, {once: true});
  } else {
    bindConnectionForm();
  }

  const requested = configuredBackend();
  const onGitHubPages = location.hostname.endsWith('.github.io');
  const initial = requested || (onGitHubPages ? '' : location.origin);
  if (!initial) {
    showConnection('Підключіть публічний HTTPS-сервер ефіру. Адреса збережеться лише у цьому браузері.');
    return;
  }
  connect(initial, Boolean(requested)).catch(error => {
    // A failed same-origin probe on localhost is the normal desktop pywebview
    // path; pywebview will inject its native API a moment later.
    if (!requested && ['localhost', '127.0.0.1'].includes(location.hostname)) return;
    showConnection(error?.message || String(error));
  });
})();
