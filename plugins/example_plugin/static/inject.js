(() => {
  const runtime = window.PlotPilotPlugins;
  if (!runtime) {
    console.warn('[example_plugin] PlotPilot runtime missing');
    return;
  }

  runtime.plugins.register({
    name: 'example_plugin',
    displayName: 'Example Plugin',
    version: '0.1.0',
    description: 'Minimal example plugin for PlotPilot plugin platform',
  });

  const host = window.__ExamplePluginHost || (window.__ExamplePluginHost = {});

  function refresh(payload = {}) {
    const novelId = payload.novelId || runtime.context.getNovelId();
    const chapterNumber = payload.chapterNumber || runtime.context.getChapterNumber();
    host.lastRefresh = {
      at: new Date().toISOString(),
      novelId,
      chapterNumber,
      payload,
    };
    console.log('[example_plugin] refresh', host.lastRefresh);
  }

  host.refresh = refresh;

  runtime.events.on('route:changed', refresh);
  runtime.events.on('chapter:loaded', refresh);
  runtime.events.on('chapter:saved', refresh);

  refresh({ source: 'startup' });
})();
