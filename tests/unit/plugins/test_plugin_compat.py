from plugins.platform.compat import build_plugin_compatibility_report, compare_versions


def test_compare_versions_handles_semver_like_values():
    assert compare_versions("0.2.1", "0.2.0") > 0
    assert compare_versions("0.2", "0.2.0") == 0
    assert compare_versions("0.1", "0.2") < 0


def test_compatibility_report_accepts_declared_compatible_manifest():
    report = build_plugin_compatibility_report(
        {
            "name": "sample",
            "runtime": {
                "api_version": "0.2",
                "frontend_runtime_version": "0.5.0",
            },
        },
        plugin_name="sample",
    )

    assert report["compatible"] is True
    assert report["status"] in {"compatible", "assumed_compatible"}
    assert report["declared"]["plugin_api_version"] == "0.2"


def test_compatibility_report_rejects_mismatched_version_requirements():
    report = build_plugin_compatibility_report(
        {
            "name": "broken",
            "plugin_api_version": "9.9",
            "frontend_runtime_version": "9.9.0",
        },
        plugin_name="broken",
    )

    assert report["compatible"] is False
    assert report["status"] == "incompatible"
    assert report["reasons"]
