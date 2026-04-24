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

  function dedupeStyles(items) {
    const seen = new Set();
    const styles = [];

    for (const href of items || []) {
      if (typeof href !== 'string' || !href.trim() || seen.has(href)) continue;
      seen.add(href);
      styles.push(href);
    }

    return styles;
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
      version: '0.4.0',
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
            runtime.events.emit('plugin:init_error', { pluginName: name, error: String(error) });
            console.warn('[PlotPilot] plugin init failed:', name, error);
          }
          return plugin;
        },
        async dispose(name) {
          const plugin = loadedPlugins.get(name);
          if (!plugin || !plugin.__plotpilotInitialized || typeof plugin.dispose !== 'function') return plugin || null;
          try {
            await plugin.dispose(runtime);
            plugin.__plotpilotInitialized = false;
            runtime.events.emit('plugin:disposed', plugin);
          } catch (error) {
            runtime.events.emit('plugin:dispose_error', { pluginName: name, error: String(error) });
            console.warn('[PlotPilot] plugin dispose failed:', name, error);
          }
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
        has(src) {
          return loadedScripts.has(src);
        },
        mark(src) {
          loadedScripts.add(src);
        },
        list() {
          return Array.from(loadedScripts.values());
        },
      },
      styles: {
        has(href) {
          return loadedStyles.has(href);
        },
        mark(href) {
          loadedStyles.add(href);
        },
        list() {
          return Array.from(loadedStyles.values());
        },
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
        currentView: null,
        currentNovelId: null,
        currentChapterNumber: null,
        lastChapterLoaded: null,
        lastChapterSaved: null,
        lastChapterCommitted: null,
        lastGenerationCompleted: null,
        lastRewriteCompleted: null,
        lastWorkbenchOpened: null,
        lastNovelSelected: null,
        lastManualRerunRequested: null,
        lastTimelineRebuildRequested: null,
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
      context: {
        getRoute() {
          return { ...runtime.state.currentRoute };
        },
        getView() {
          return runtime.state.currentView || null;
        },
        getNovelId() {
          if (runtime.state.currentNovelId) return runtime.state.currentNovelId;
          const match = window.location.pathname.match(/\/book\/([^/]+)/);
          if (match) return decodeURIComponent(match[1]);
          const params = new URLSearchParams(window.location.search);
          return params.get('novel') || null;
        },
        getChapterNumber() {
          if (Number.isFinite(runtime.state.currentChapterNumber) && runtime.state.currentChapterNumber > 0) {
            return runtime.state.currentChapterNumber;
          }
          const params = new URLSearchParams(window.location.search);
          const value = Number(params.get('chapter'));
          return Number.isFinite(value) && value > 0 ? value : null;
        },
        getCurrentChapter() {
          const chapterNumber = runtime.context.getChapterNumber();
          if (!Number.isFinite(chapterNumber) || chapterNumber <= 0) return null;
          return {
            novelId: runtime.context.getNovelId(),
            chapterNumber,
          };
        },
        getLastEvent(eventName) {
          const eventStateMap = {
            'chapter:loaded': runtime.state.lastChapterLoaded,
            'chapter:saved': runtime.state.lastChapterSaved,
            'chapter:committed': runtime.state.lastChapterCommitted,
            'generation:completed': runtime.state.lastGenerationCompleted,
            'rewrite:completed': runtime.state.lastRewriteCompleted,
            'workbench:opened': runtime.state.lastWorkbenchOpened,
            'novel:selected': runtime.state.lastNovelSelected,
            'manual:rerun_requested': runtime.state.lastManualRerunRequested,
            'timeline:rebuild_requested': runtime.state.lastTimelineRebuildRequested,
          };
          return Object.prototype.hasOwnProperty.call(eventStateMap, eventName)
            ? eventStateMap[eventName]
            : null;
        },
        getAvailableEvents() {
          return [
            'route:changed',
            'chapter:loaded',
            'chapter:saved',
            'chapter:committed',
            'generation:completed',
            'rewrite:completed',
            'workbench:opened',
            'novel:selected',
            'manual:rerun_requested',
            'timeline:rebuild_requested',
          ];
        },
        getContext() {
          return {
            route: { ...runtime.state.currentRoute },
            view: runtime.context.getView(),
            novelId: runtime.context.getNovelId(),
            chapterNumber: runtime.context.getChapterNumber(),
            currentChapter: runtime.context.getCurrentChapter(),
            startedAt: runtime.state.startedAt,
            lastChapterLoaded: runtime.state.lastChapterLoaded,
            lastChapterSaved: runtime.state.lastChapterSaved,
            lastChapterCommitted: runtime.state.lastChapterCommitted,
            lastGenerationCompleted: runtime.state.lastGenerationCompleted,
            lastRewriteCompleted: runtime.state.lastRewriteCompleted,
            lastWorkbenchOpened: runtime.state.lastWorkbenchOpened,
            lastNovelSelected: runtime.state.lastNovelSelected,
            lastManualRerunRequested: runtime.state.lastManualRerunRequested,
            lastTimelineRebuildRequested: runtime.state.lastTimelineRebuildRequested,
          };
        },
      },
      actions: {
        refreshManifest: async () => {
          const manifest = await runtime.fetchJson(MANIFEST_ENDPOINT);
          runtime.state.manifest = manifest;
          registerManifestPlugins(runtime, manifest && manifest.items);
          const styles = dedupeStyles(manifest && manifest.frontend_styles);
          for (const href of styles) {
            loadStyle(runtime, href);
          }
          const scripts = dedupeScripts(manifest && manifest.frontend_scripts);
          for (const src of scripts) {
            loadScript(runtime, src);
          }
          runtime.events.emit('manifest:loaded', manifest);
          return manifest;
        },
        reloadPlugins: async () => {
          const pluginsPayload = await runtime.fetchJson(PLUGINS_ENDPOINT);
          runtime.state.pluginsPayload = pluginsPayload;
          runtime.events.emit('plugins:loaded', pluginsPayload);
          return pluginsPayload;
        },
      },
      host: {
        emitChapterSaved(payload) {
          runtime.state.lastChapterSaved = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('chapter:saved', payload);
          runtime.events.emit('chapter:saved', payload);
        },
        emitChapterLoaded(payload) {
          runtime.state.lastChapterLoaded = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('chapter:loaded', payload);
          runtime.events.emit('chapter:loaded', payload);
        },
        emitChapterCommitted(payload) {
          runtime.state.lastChapterCommitted = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('chapter:committed', payload);
          runtime.events.emit('chapter:committed', payload);
        },
        emitGenerationCompleted(payload) {
          runtime.state.lastGenerationCompleted = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('generation:completed', payload);
          runtime.events.emit('generation:completed', payload);
        },
        emitRewriteCompleted(payload) {
          runtime.state.lastRewriteCompleted = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('rewrite:completed', payload);
          runtime.events.emit('rewrite:completed', payload);
        },
        emitNovelChanged(payload) {
          runtime.host.updateContext(payload);
          runtime.hooks.emit('novel:changed', payload);
          runtime.events.emit('novel:changed', payload);
        },
        emitWorkbenchOpened(payload) {
          runtime.state.lastWorkbenchOpened = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('workbench:opened', payload);
          runtime.events.emit('workbench:opened', payload);
        },
        emitNovelSelected(payload) {
          runtime.state.lastNovelSelected = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('novel:selected', payload);
          runtime.events.emit('novel:selected', payload);
        },
        emitManualRerunRequested(payload) {
          runtime.state.lastManualRerunRequested = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('manual:rerun_requested', payload);
          runtime.events.emit('manual:rerun_requested', payload);
        },
        emitTimelineRebuildRequested(payload) {
          runtime.state.lastTimelineRebuildRequested = payload || null;
          runtime.host.updateContext(payload);
          runtime.hooks.emit('timeline:rebuild_requested', payload);
          runtime.events.emit('timeline:rebuild_requested', payload);
        },
        emitRouteChanged(payload) {
          runtime.state.currentRoute = {
            path: payload?.path || window.location.pathname,
            query: payload?.query || window.location.search,
            hash: payload?.hash || window.location.hash,
          };
          runtime.host.updateContext(payload);
          runtime.hooks.emit('route:changed', runtime.state.currentRoute);
          runtime.events.emit('route:changed', runtime.state.currentRoute);
        },
        updateContext(payload) {
          if (!payload || typeof payload !== 'object') return;
          const novelId = payload.novelId || payload.novel_id || payload.bookId || payload.book_id || null;
          const rawChapterNumber = payload.chapterNumber ?? payload.chapter_number ?? payload.chapter ?? null;
          const chapterNumber = Number(rawChapterNumber);
          const view = payload.view || payload.routeName || payload.page || null;
          if (typeof novelId === 'string' && novelId.trim()) {
            runtime.state.currentNovelId = novelId.trim();
          }
          if (Number.isFinite(chapterNumber) && chapterNumber > 0) {
            runtime.state.currentChapterNumber = chapterNumber;
          }
          if (typeof view === 'string' && view.trim()) {
            runtime.state.currentView = view.trim();
          }
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
    if (document.querySelector(`link[data-plugin-style="${href}"]`)) {
      runtime.styles.mark(href);
      return;
    }

    const link = document.createElement('link');
    link.dataset.pluginStyle = href;
    link.dataset.pluginKey = `plotpilot-plugin-style-${href}`;
    link.rel = 'stylesheet';
    link.href = href;
    link.addEventListener('load', () => {
      runtime.styles.mark(href);
      runtime.events.emit('style:loaded', { href });
    });
    link.addEventListener('error', () => {
      runtime.events.emit('style:error', { href });
    });
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
      await runtime.actions.refreshManifest();
    } catch (error) {
      console.warn('[PlotPilot] plugin manifest load skipped:', error);
      runtime.events.emit('manifest:error', { error: String(error) });
    }

    try {
      await runtime.actions.reloadPlugins();
    } catch (error) {
      runtime.events.emit('plugins:error', { error: String(error) });
    }

    runtime.host.emitRouteChanged({ source: 'startup' });
  }

  start();
})();
