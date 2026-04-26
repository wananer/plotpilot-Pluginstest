(function registerSampleHelloPlugin() {
  const runtime = window.PlotPilotPlugins;
  if (!runtime) {
    console.warn('[SampleHello] PlotPilotPlugins runtime missing');
    return;
  }
  if (window.__sampleHelloPluginLoaded) return;
  window.__sampleHelloPluginLoaded = true;

  function dismissBadge(badge) {
    if (badge.dataset.dismissScheduled === 'true') return;
    badge.dataset.dismissScheduled = 'true';
    window.setTimeout(() => {
      badge.style.opacity = '0';
      badge.style.transform = 'translateY(8px)';
      window.setTimeout(() => badge.remove(), 220);
    }, 3000);
  }

  function ensureBadge() {
    const existingBadge = document.getElementById('sample-hello-plugin-badge');
    if (existingBadge) {
      dismissBadge(existingBadge);
      return;
    }

    const badge = document.createElement('div');
    badge.id = 'sample-hello-plugin-badge';
    badge.textContent = 'Sample Hello 插件已加载';
    Object.assign(badge.style, {
      position: 'fixed',
      left: '16px',
      bottom: '16px',
      zIndex: '9999',
      padding: '8px 12px',
      borderRadius: '999px',
      background: 'rgba(16, 185, 129, 0.92)',
      color: '#fff',
      fontSize: '12px',
      fontWeight: '700',
      boxShadow: '0 8px 20px rgba(0,0,0,0.18)',
      pointerEvents: 'none',
      transition: 'opacity 180ms ease, transform 180ms ease'
    });
    document.body.appendChild(badge);
    dismissBadge(badge);
  }

  runtime.plugins.register({
    name: 'sample_hello',
    display_name: 'Sample Hello',
    version: '0.1.0',
    setup() {
      ensureBadge();
    },
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureBadge, { once: true });
  } else {
    ensureBadge();
  }
})();
