(function loadPlotPilotPlugins() {
  const MANIFEST_ENDPOINT = '/api/v1/plugins/manifest';
  const PLUGINS_ENDPOINT = '/api/v1/plugins';

  function dedupeScripts(items) {
    const seen = new Set();
    const scripts = [];

    for (const src of items || []) {
      if (typeof src !== 'string' || !src.trim() || seen.has(src)) continue;
      seen.add(src);
      scripts.push(src);
    }

    return scripts;
  }

  function createRuntime() {
    if (window.PlotPilotPlugins) {
      return window.PlotPilotPlugins;
    }

    const listeners = new Map();
    const loadedScripts = new Set();
    const loadedStyles = new Set();
    const loadedPlugins = new Map();
    const pluginSettings = new Map();

    const runtime = {
      version: '0.5.0',
      endpoints: {
        manifest: MANIFEST_ENDPOINT,
        plugins: PLUGINS_ENDPOINT,
      },
      events: {
        on(eventName, handler) {
          if (!eventName || typeof handler !== 'function') return () => {};
          if (!listeners.has(eventName)) listeners.set(eventName, new Set());
          listeners.get(eventName).add(handler);
          return () => listeners.get(eventName)?.delete(handler);
        },
        once(eventName, handler) {
          if (!eventName || typeof handler !== 'function') return () => {};
          const off = runtime.events.on(eventName, (payload) => {
            off();
            handler(payload);
          });
          return off;
        },
        emit(eventName, payload) {
          const handlers = listeners.get(eventName);
          if (!handlers) return;
          for (const handler of handlers) {
            try {
              handler(payload);
            } catch (error) {
              console.warn('[PlotPilot] plugin event handler error:', eventName, error);
            }
          }
        },
      },
      settings: {
        get(pluginName, key, fallback = null) {
          const values = pluginSettings.get(pluginName) || {};
          return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : fallback;
        },
        set(pluginName, key, value) {
          const values = { ...(pluginSettings.get(pluginName) || {}) };
          values[key] = value;
          pluginSettings.set(pluginName, values);
          runtime.events.emit('settings:changed', { pluginName, key, value, values });
          return value;
        },
        all(pluginName) {
          return { ...(pluginSettings.get(pluginName) || {}) };
        },
      },
      plugins: {
        register(plugin) {
          if (!plugin || !plugin.name) return;
          const existing = loadedPlugins.get(plugin.name) || {};
          const nextPlugin = { ...existing, ...plugin, name: plugin.name };
          loadedPlugins.set(plugin.name, nextPlugin);
          runtime.events.emit(existing.name ? 'plugin:updated' : 'plugin:registered', nextPlugin);
          queueMicrotask(() => runtime.plugins.init(plugin.name));
          return nextPlugin;
        },
        async init(name) {
          const plugin = loadedPlugins.get(name);
          if (!plugin || plugin.__plotpilotInitialized || typeof plugin.init !== 'function') return plugin || null;
          plugin.__plotpilotInitialized = true;
          try {
            await plugin.init(runtime);
            runtime.events.emit('plugin:initialized', plugin);
          } catch (error) {
            plugin.__plotpilotInitialized = false;
            console.warn('[PlotPilot] plugin init failed:', name, error);
          }
          return plugin;
        },
        async dispose(name) {
          const plugin = loadedPlugins.get(name);
          if (!plugin || !plugin.__plotpilotInitialized || typeof plugin.dispose !== 'function') return plugin || null;
          await plugin.dispose(runtime);
          plugin.__plotpilotInitialized = false;
          runtime.events.emit('plugin:disposed', plugin);
          return plugin;
        },
        list() {
          return Array.from(loadedPlugins.values());
        },
        get(name) {
          return loadedPlugins.get(name) || null;
        },
      },
      scripts: {
        has(src) { return loadedScripts.has(src); },
        mark(src) { loadedScripts.add(src); },
        list() { return Array.from(loadedScripts.values()); },
      },
      styles: {
        has(href) { return loadedStyles.has(href); },
        mark(href) { loadedStyles.add(href); },
        list() { return Array.from(loadedStyles.values()); },
      },
      state: {
        manifest: null,
        pluginsPayload: null,
        startedAt: new Date().toISOString(),
        currentRoute: {
          path: window.location.pathname,
          query: window.location.search,
          hash: window.location.hash,
        },
      },
      hooks: {
        emit(name, payload) {
          runtime.events.emit(`hook:${name}`, payload);
        },
        on(name, handler) {
          return runtime.events.on(`hook:${name}`, handler);
        },
        once(name, handler) {
          return runtime.events.once(`hook:${name}`, handler);
        },
      },
      host: {
        emitChapterSaved(payload) {
          runtime.hooks.emit('chapter:saved', payload);
          runtime.events.emit('chapter:saved', payload);
        },
        emitChapterLoaded(payload) {
          runtime.hooks.emit('chapter:loaded', payload);
          runtime.events.emit('chapter:loaded', payload);
        },
        emitRouteChanged(payload) {
          runtime.state.currentRoute = {
            path: payload?.path || window.location.pathname,
            query: payload?.query || window.location.search,
            hash: payload?.hash || window.location.hash,
          };
          runtime.hooks.emit('route:changed', runtime.state.currentRoute);
          runtime.events.emit('route:changed', runtime.state.currentRoute);
        },
      },
      context: {
        getRoute() {
          return { ...runtime.state.currentRoute };
        },
        getNovelId() {
          const match = window.location.pathname.match(/\/book\/([^/]+)/);
          if (match) return decodeURIComponent(match[1]);
          const params = new URLSearchParams(window.location.search);
          return params.get('novel') || null;
        },
        getChapterNumber() {
          const params = new URLSearchParams(window.location.search);
          const value = Number(params.get('chapter'));
          return Number.isFinite(value) && value > 0 ? value : null;
        },
      },
      async fetchJson(url) {
        const response = await fetch(url, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) {
          throw new Error(`Plugin request failed: ${response.status}`);
        }
        return response.json();
      },
    };

    window.PlotPilotPlugins = runtime;
    return runtime;
  }

  function loadScript(runtime, src) {
    if (!src || typeof src !== 'string') return;
    if (runtime.scripts.has(src)) return;
    if (document.querySelector(`script[data-plugin-src="${src}"]`)) {
      runtime.scripts.mark(src);
      return;
    }

    const script = document.createElement('script');
    script.dataset.pluginSrc = src;
    script.dataset.pluginKey = `plotpilot-plugin-${src}`;
    script.src = src;
    script.async = true;
    script.addEventListener('load', () => {
      runtime.scripts.mark(src);
      runtime.events.emit('script:loaded', { src });
    });
    script.addEventListener('error', () => {
      runtime.events.emit('script:error', { src });
    });
    document.body.appendChild(script);
  }

  function loadStyle(runtime, href) {
    if (!href || typeof href !== 'string') return;
    if (runtime.styles.has(href)) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.dataset.pluginStyle = href;
    link.addEventListener('load', () => runtime.styles.mark(href));
    document.head.appendChild(link);
  }

  function registerManifestPlugins(runtime, items) {
    for (const item of items || []) {
      if (!item || !item.name) continue;
      runtime.plugins.register({
        name: item.name,
        display_name: item.display_name || item.name,
        version: item.version || null,
        enabled: item.enabled !== false,
        frontend_scripts: Array.isArray(item.frontend_scripts) ? item.frontend_scripts : [],
        frontend_styles: Array.isArray(item.frontend_styles) ? item.frontend_styles : [],
        capabilities: item.capabilities || {},
        permissions: Array.isArray(item.permissions) ? item.permissions : [],
        hooks: Array.isArray(item.hooks) ? item.hooks : [],
        manifest: item.manifest || {},
      });
    }
  }

  function patchHistory(runtime) {
    if (window.__plotpilot_plugin_history_patched__) return;
    window.__plotpilot_plugin_history_patched__ = true;

    const wrap = (methodName) => {
      const original = history[methodName];
      if (typeof original !== 'function') return;
      history[methodName] = function patchedHistoryMethod(...args) {
        const result = original.apply(this, args);
        queueMicrotask(() => {
          runtime.host.emitRouteChanged({
            path: window.location.pathname,
            query: window.location.search,
            hash: window.location.hash,
            source: methodName,
          });
        });
        return result;
      };
    };

    wrap('pushState');
    wrap('replaceState');
    window.addEventListener('popstate', () => runtime.host.emitRouteChanged({ source: 'popstate' }));
    window.addEventListener('hashchange', () => runtime.host.emitRouteChanged({ source: 'hashchange' }));
  }

  async function start() {
    const runtime = createRuntime();
    patchHistory(runtime);
    runtime.events.emit('runtime:ready', {
      version: runtime.version,
      endpoints: runtime.endpoints,
    });

    try {
      const manifest = await runtime.fetchJson(MANIFEST_ENDPOINT);
      runtime.state.manifest = manifest;
      registerManifestPlugins(runtime, manifest && manifest.items);
      for (const href of dedupeScripts(manifest && manifest.frontend_styles)) {
        loadStyle(runtime, href);
      }
      const scripts = dedupeScripts(manifest && manifest.frontend_scripts);
      for (const src of scripts) {
        loadScript(runtime, src);
      }
      runtime.events.emit('manifest:loaded', manifest);
    } catch (error) {
      console.warn('[PlotPilot] plugin manifest load skipped:', error);
      runtime.events.emit('manifest:error', { error: String(error) });
    }

    try {
      const pluginsPayload = await runtime.fetchJson(PLUGINS_ENDPOINT);
      runtime.state.pluginsPayload = pluginsPayload;
      runtime.events.emit('plugins:loaded', pluginsPayload);
    } catch (error) {
      runtime.events.emit('plugins:error', { error: String(error) });
    }

    runtime.host.emitRouteChanged({ source: 'startup' });
  }

  start();
})();
