"""Compatibility helpers for the PlotPilot plugin platform."""
from __future__ import annotations

import re
from typing import Any

PLATFORM_RUNTIME_API_VERSION = "0.2"
FRONTEND_RUNTIME_VERSION = "0.5.0"

_VERSION_RE = re.compile(r"\d+")


def _normalize_version(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _version_tuple(value: Any) -> tuple[int, ...] | None:
    text = _normalize_version(value)
    if not text:
        return None
    parts = [int(part) for part in _VERSION_RE.findall(text)]
    if not parts:
        return None
    return tuple(parts)


def compare_versions(left: Any, right: Any) -> int:
    left_tuple = _version_tuple(left)
    right_tuple = _version_tuple(right)
    if left_tuple is None or right_tuple is None:
        left_text = _normalize_version(left) or ""
        right_text = _normalize_version(right) or ""
        if left_text == right_text:
            return 0
        return -1 if left_text < right_text else 1

    width = max(len(left_tuple), len(right_tuple))
    padded_left = left_tuple + (0,) * (width - len(left_tuple))
    padded_right = right_tuple + (0,) * (width - len(right_tuple))
    if padded_left == padded_right:
        return 0
    return -1 if padded_left < padded_right else 1


def _extract_declared_versions(manifest: dict[str, Any]) -> dict[str, str | None]:
    runtime = manifest.get("runtime") if isinstance(manifest, dict) else None
    runtime_dict = runtime if isinstance(runtime, dict) else {}
    return {
        "plugin_api_version": _normalize_version(manifest.get("plugin_api_version") or runtime_dict.get("api_version")),
        "host_min_version": _normalize_version(manifest.get("host_min_version") or runtime_dict.get("host_min_version")),
        "host_max_version": _normalize_version(manifest.get("host_max_version") or runtime_dict.get("host_max_version")),
        "frontend_runtime_version": _normalize_version(
            manifest.get("frontend_runtime_version") or runtime_dict.get("frontend_runtime_version")
        ),
    }


def build_plugin_compatibility_report(
    manifest: dict[str, Any],
    *,
    plugin_name: str,
    host_runtime_api_version: str = PLATFORM_RUNTIME_API_VERSION,
    frontend_runtime_version: str = FRONTEND_RUNTIME_VERSION,
) -> dict[str, Any]:
    declared = _extract_declared_versions(manifest)
    plugin_api_version = declared["plugin_api_version"]
    host_min_version = declared["host_min_version"]
    host_max_version = declared["host_max_version"]
    required_frontend_runtime_version = declared["frontend_runtime_version"]

    reasons: list[str] = []
    warnings: list[str] = []
    range_hint = host_min_version is not None or host_max_version is not None

    if plugin_api_version is None and not range_hint and required_frontend_runtime_version is None:
        warnings.append("manifest does not declare plugin_api_version, host_min_version/host_max_version, or frontend_runtime_version")

    if host_min_version is not None and compare_versions(host_runtime_api_version, host_min_version) < 0:
        reasons.append(f"host_runtime_api_version {host_runtime_api_version} is lower than host_min_version {host_min_version}")

    if host_max_version is not None and compare_versions(host_runtime_api_version, host_max_version) > 0:
        reasons.append(f"host_runtime_api_version {host_runtime_api_version} is higher than host_max_version {host_max_version}")

    if plugin_api_version is not None:
        exact_match = compare_versions(host_runtime_api_version, plugin_api_version) == 0
        range_compatible = True
        if host_min_version is not None:
            range_compatible = range_compatible and compare_versions(host_runtime_api_version, host_min_version) >= 0
        if host_max_version is not None:
            range_compatible = range_compatible and compare_versions(host_runtime_api_version, host_max_version) <= 0
        if not exact_match and not range_compatible:
            reasons.append(
                f"plugin_api_version {plugin_api_version} is not compatible with host_runtime_api_version {host_runtime_api_version}"
            )

    if required_frontend_runtime_version is not None and compare_versions(frontend_runtime_version, required_frontend_runtime_version) != 0:
        reasons.append(
            f"frontend_runtime_version {frontend_runtime_version} does not match required_frontend_runtime_version {required_frontend_runtime_version}"
        )

    status = "compatible"
    if reasons:
        status = "incompatible"
    elif warnings:
        status = "assumed_compatible"

    return {
        "plugin_name": plugin_name,
        "status": status,
        "compatible": not reasons,
        "declared": declared,
        "current": {
            "host_runtime_api_version": host_runtime_api_version,
            "frontend_runtime_version": frontend_runtime_version,
        },
        "reasons": reasons,
        "warnings": warnings,
    }
