from infrastructure.ai.prompt_resolver import PromptResolver


def test_prompt_resolver_prefers_db_over_json_and_fallback(monkeypatch):
    resolver = PromptResolver()
    monkeypatch.setattr(
        resolver,
        "_render_from_db",
        lambda key, variables: {"system": "db system {x}".format(**variables), "user": "db user"},
    )
    monkeypatch.setattr(
        resolver,
        "_render_from_json",
        lambda key, variables: {"system": "json system", "user": "json user"},
    )

    resolved = resolver.render(
        "demo",
        {"x": "value"},
        fallback_system="fallback system",
        fallback_user="fallback user",
    )

    assert resolved.source == "prompt_manager"
    assert resolved.system == "db system value"
    assert resolved.user == "db user"


def test_prompt_resolver_falls_back_to_json_then_caller_fallback(monkeypatch):
    resolver = PromptResolver()
    monkeypatch.setattr(resolver, "_render_from_db", lambda key, variables: None)
    monkeypatch.setattr(
        resolver,
        "_render_from_json",
        lambda key, variables: {"system": "", "user": "json user"},
    )

    resolved = resolver.render("demo", {}, fallback_system="fallback system")

    assert resolved.source == "prompt_loader"
    assert resolved.system == "fallback system"
    assert resolved.user == "json user"

    monkeypatch.setattr(resolver, "_render_from_json", lambda key, variables: None)
    resolved = resolver.render("demo", {}, fallback_system="s", fallback_user="u")
    assert resolved.source == "fallback"
    assert resolved.system == "s"
    assert resolved.user == "u"
