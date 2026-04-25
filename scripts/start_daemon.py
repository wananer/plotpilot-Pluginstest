"""Minimal daemon bootstrap for the PlotPilot plugin platform."""

from __future__ import annotations

from plugins.loader import init_daemon_plugins


def main() -> list[str]:
    loaded_plugins = init_daemon_plugins()
    if loaded_plugins:
        print("Loaded daemon plugins:", ", ".join(loaded_plugins))
    else:
        print("No daemon plugins loaded")
    return loaded_plugins


if __name__ == "__main__":
    main()
