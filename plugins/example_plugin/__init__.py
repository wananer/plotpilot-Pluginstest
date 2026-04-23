def init_api(app):
    app.state.example_plugin_initialized = getattr(app.state, 'example_plugin_initialized', 0) + 1


def init_daemon():
    return 'example_plugin:daemon_initialized'
