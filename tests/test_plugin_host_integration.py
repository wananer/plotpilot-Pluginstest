from plugins.platform.hook_dispatcher import clear_hooks, register_hook
from plugins.platform.host_integration import build_generation_context_patch


def test_generation_context_patch_skips_disabled_hooks(monkeypatch):
    clear_hooks()

    def handler(payload):
        raise AssertionError("disabled plugin hooks should not run")

    register_hook("sample_state_plugin", "before_context_build", handler)
    monkeypatch.setattr(
        "plugins.platform.hook_dispatcher._plugin_is_enabled",
        lambda plugin_name: False,
    )

    assert build_generation_context_patch("novel-1", 1, "outline") == ""
    clear_hooks()
