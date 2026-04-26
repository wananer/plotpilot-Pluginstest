#!/usr/bin/env python3
"""End-to-end smoke test for the PlotPilot plugin platform and Evolution plugin.

The smoke run uses an isolated plugin data directory, exercises the platform
loader/enable switch/static serving, then calls every public Evolution service
operation at least once. It writes a JSON report under .omx/artifacts.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class PaletteStructuredProvider:
    async def extract(self, request: dict[str, Any]) -> dict[str, Any]:
        chapter_number = int(request.get("chapter_number") or 0)
        return {
            "summary": f"秋明月在第{chapter_number}章完成街头演出，红美玲仍是她的安全锚点。",
            "characters": [
                {
                    "name": "秋明月",
                    "summary": "在街头舞台短暂恢复自我",
                    "aliases": ["明月"],
                    "locations": ["夜街", "舞台"],
                    "appearance": {
                        "summary": "黑色短发，舞台上常穿宽松外套和磨旧靴子。",
                        "features": ["黑色短发", "舞台眼线"],
                        "style": ["随意舒适", "摇滚感"],
                        "current_outfit": "宽松外套与磨旧靴子",
                    },
                    "attributes": [
                        {"category": "基础", "name": "身份", "value": "贵族学校大小姐", "description": "校内需要维持优秀形象"},
                        {"category": "音乐", "name": "擅长", "value": "吉他solo"},
                    ],
                    "world_profile": {
                        "schema_name": "现代校园摇滚",
                        "fields": [
                            {"category": "学校", "name": "校内伪装", "value": "优秀的大小姐"},
                            {"category": "关系", "name": "核心依赖", "value": "红美玲"},
                        ],
                    },
                    "personality_palette": {
                        "metaphor": "人的性格就像调色盘，叛逆是底色，热情与不拘一格是主色调。",
                        "base": "叛逆",
                        "main_tones": ["热情", "不拘一格"],
                        "accents": ["依赖"],
                        "derivatives": [
                            {
                                "tone": "热情",
                                "title": "摇滚燃烧",
                                "description": "创作、演唱和练习都会投入百分百热情。",
                                "trigger": "面对摇滚",
                            },
                            {
                                "tone": "依赖",
                                "title": "崩溃时靠近",
                                "description": "压力过大时会抓住红美玲的衣角寻求依靠。",
                                "visibility": "只在两人或崩溃时显露",
                            },
                        ],
                    },
                    "known_facts": ["红美玲会在台下等待她"],
                    "unknowns": ["不知道后台机关的真正触发条件"],
                    "misbeliefs": ["误以为临时变奏不会影响队友节奏"],
                    "emotion": "热烈之后带着疲惫",
                    "inner_change": "在掌声里短暂卸下大小姐伪装",
                    "growth_stage": "从独自硬撑转向承认依赖",
                    "growth_change": "开始允许红美玲看见自己的疲惫",
                    "capability_limits": ["不能凭空破解后台机关"],
                    "decision_biases": ["压力过大时会本能靠近红美玲"],
                }
            ],
            "locations": ["夜街", "舞台"],
            "world_events": [
                {
                    "summary": f"秋明月在第{chapter_number}章于夜街舞台用吉他solo",
                    "event_type": "performance",
                    "characters": ["秋明月"],
                    "locations": ["夜街"],
                    "known_facts": ["红美玲会在台下等待她"],
                    "unknowns": ["不知道后台机关的真正触发条件"],
                    "capability_limits": ["不能凭空破解后台机关"],
                }
            ],
        }


class FailingStructuredProvider:
    async def extract(self, request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("smoke provider intentionally offline")


class SmokeRunner:
    def __init__(self, report_dir: Path) -> None:
        self.report_dir = report_dir
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, func: Callable[[], Any]) -> Any:
        started_at = datetime.now(timezone.utc)
        try:
            result = func()
            self.checks.append(
                {
                    "name": name,
                    "ok": True,
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "detail": _compact(result),
                }
            )
            return result
        except Exception as exc:
            self.checks.append(
                {
                    "name": name,
                    "ok": False,
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            raise

    async def check_async(self, name: str, func: Callable[[], Any]) -> Any:
        started_at = datetime.now(timezone.utc)
        try:
            result = await func()
            self.checks.append(
                {
                    "name": name,
                    "ok": True,
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "detail": _compact(result),
                }
            )
            return result
        except Exception as exc:
            self.checks.append(
                {
                    "name": name,
                    "ok": False,
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            raise

    def write_report(self, *, runtime_root: Path) -> Path:
        payload = {
            "ok": all(item["ok"] for item in self.checks),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime_root": str(runtime_root),
            "checks": self.checks,
            "summary": {
                "total": len(self.checks),
                "passed": sum(1 for item in self.checks if item["ok"]),
                "failed": sum(1 for item in self.checks if not item["ok"]),
            },
        }
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.report_dir / "smoke_report.json"
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return report_path


async def run_smoke() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_dir = PROJECT_ROOT / ".omx" / "artifacts" / f"evolution-smoke-{timestamp}"
    runner = SmokeRunner(report_dir)

    with tempfile.TemporaryDirectory(prefix="plotpilot-evolution-smoke-") as temp_dir:
        runtime_root = Path(temp_dir)
        os.environ["AITEXT_PROD_DATA_DIR"] = str(runtime_root / "plotpilot-data")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import plugins.loader as plugin_loader
        from plugins.platform.hook_dispatcher import clear_hooks, dispatch_hook, list_hooks
        from plugins.platform.host_integration import (
            build_generation_context_patch,
            collect_chapter_review_context_with_plugins,
            collect_story_planning_context_with_plugins,
            notify_chapter_committed,
            notify_chapter_review_completed,
            notify_novel_created_with_plugins,
            review_chapter_with_plugins,
        )
        from plugins.platform.job_registry import PluginJobRegistry
        from plugins.platform.plugin_storage import PluginStorage
        from plugins.world_evolution_core.continuity import analyze_chapter_transitions
        from plugins.world_evolution_core.service import EvolutionWorldAssistantService

        plugin_loader._PLUGIN_CONTROL_PATH = runtime_root / "plugin_controls.json"
        plugin_loader.set_plugin_enabled("world_evolution_core", True)
        clear_hooks()

        app = FastAPI()
        app.include_router(plugin_loader.create_plugin_manifest_router(), prefix="/api/v1")
        initialized = runner.check("platform.init_api_plugins", lambda: plugin_loader.init_api_plugins(app))
        assert "world_evolution_core" in initialized, initialized

        storage = PluginStorage(root=runtime_root / "plugin_platform")
        service = EvolutionWorldAssistantService(
            storage=storage,
            jobs=PluginJobRegistry(storage),
            extractor_provider=PaletteStructuredProvider(),
        )

        import plugins.world_evolution_core.routes as evolution_routes

        evolution_routes._service = service
        client = TestClient(app)

        manifest = runner.check("platform.manifest_lists_evolution", lambda: client.get("/api/v1/plugins/manifest"))
        assert manifest.status_code == 200, manifest.text
        manifest_payload = manifest.json()
        evolution_manifest = next(item for item in manifest_payload["items"] if item["name"] == "world_evolution_core")
        assert evolution_manifest["enabled"] is True
        assert "evolution-world" in evolution_manifest.get("route_aliases", [])
        assert any("/plugins/world_evolution_core/static/inject.js" in item for item in manifest_payload["frontend_scripts"])

        platform_status = runner.check("platform.status_endpoint", lambda: client.get("/api/v1/plugins/platform/status"))
        assert platform_status.status_code == 200
        assert platform_status.json()["features"]["plugin_storage"] is True

        hooks_response = runner.check("platform.hooks_registered", lambda: client.get("/api/v1/plugins/platform/hooks"))
        assert hooks_response.status_code == 200
        registered_hooks = hooks_response.json()["items"]
        expected_hooks = set(evolution_manifest["hooks"])
        assert expected_hooks <= set(registered_hooks), registered_hooks

        status_response = runner.check("evolution.route_status", lambda: client.get("/api/v1/plugins/evolution-world/status"))
        assert status_response.status_code == 200
        assert "prehistory_worldline" in status_response.json()["capabilities"]
        assert "global_route_map" in status_response.json()["capabilities"]

        static_response = runner.check("platform.static_serves_evolution_asset", lambda: client.get("/plugins/world_evolution_core/static/inject.js"))
        assert static_response.status_code == 200
        assert "Evolution" in static_response.text

        disable_response = runner.check(
            "platform.disable_evolution_blocks_routes_static_and_hooks",
            lambda: client.put("/api/v1/plugins/world_evolution_core/enabled", json={"enabled": False}),
        )
        assert disable_response.status_code == 200
        assert client.get("/api/v1/plugins/evolution-world/status").status_code == 403
        assert client.get("/plugins/world_evolution_core/static/inject.js").status_code == 403
        skipped = await dispatch_hook("before_context_build", {"novel_id": "smoke-disabled", "chapter_number": 1})
        assert skipped and skipped[0]["skipped"] is True

        enable_response = runner.check(
            "platform.enable_evolution_restores_routes_static_and_hooks",
            lambda: client.put("/api/v1/plugins/world_evolution_core/enabled", json={"enabled": True}),
        )
        assert enable_response.status_code == 200
        assert client.get("/api/v1/plugins/evolution-world/status").status_code == 200
        assert client.get("/plugins/world_evolution_core/static/inject.js").status_code == 200
        assert "world_evolution_core" in list_hooks().get("after_commit", [])

        novel_id = "smoke-rock-school"
        prehistory = await runner.check_async(
            "evolution.after_novel_created",
            lambda: service.after_novel_created(
                {
                    "novel_id": novel_id,
                    "payload": {
                        "title": "夜街变奏",
                        "genre": "校园摇滚悬疑",
                        "premise": "贵族学校大小姐在夜街乐队中寻找真实自我，并调查旧舞台事故。",
                        "target_chapters": 240,
                        "style_hint": "诗性但克制，重视人物动作与伏笔。",
                    },
                }
            ),
        )
        assert prehistory["ok"] is True
        assert service.repository.get_prehistory_worldline(novel_id)["depth"]["horizon_years"] >= 12

        planning = runner.check(
            "evolution.before_story_planning",
            lambda: service.before_story_planning(
                {"novel_id": novel_id, "payload": {"purpose": "macro_outline", "style_hint": "冷峻短句，动作推动伏笔。"}}
            ),
        )
        assert planning["ok"] is True
        assert "故事开始前的世界线" in planning["context_blocks"][0]["content"]

        for chapter in range(1, 11):
            await runner.check_async(
                f"evolution.after_commit_chapter_{chapter}",
                lambda chapter=chapter: service.after_commit(
                    {
                        "novel_id": novel_id,
                        "chapter_number": chapter,
                        "payload": {
                            "content": (
                                f"《秋明月》第{chapter}次在夜街舞台用吉他solo，红美玲在台下看着她。"
                                "结尾时秋明月留在舞台边，后台机关仍未解开。"
                            )
                        },
                    }
                ),
            )

        card = runner.check("evolution.get_character", lambda: service.get_character(novel_id, "秋明月"))
        assert card is not None
        assert card["appearance"]["summary"].startswith("黑色短发")
        assert card["personality_palette"]["base"] == "叛逆"

        context = runner.check(
            "evolution.before_context_build",
            lambda: service.before_context_build(
                {"novel_id": novel_id, "chapter_number": 11, "payload": {"outline": "秋明月追查后台机关，红美玲被迫介入。"}}
            ),
        )
        assert context["ok"] is True
        assert "最近10章大总结" in context["context_blocks"][0]["content"]
        assert "性格调色盘" in context["context_blocks"][0]["content"]

        review_context = runner.check(
            "evolution.before_chapter_review",
            lambda: service.before_chapter_review(
                {
                    "novel_id": novel_id,
                    "chapter_number": 11,
                    "payload": {"content": "秋明月一眼看穿后台机关的真正触发条件，并独自破解。"},
                }
            ),
        )
        assert review_context["ok"] is True
        assert review_context["data"]["review_context_blocks"]

        review = runner.check(
            "evolution.review_chapter",
            lambda: service.review_chapter(
                {
                    "novel_id": novel_id,
                    "chapter_number": 11,
                    "payload": {"content": "秋明月一眼看穿后台机关的真正触发条件，并独自破解。"},
                }
            ),
        )
        assert review["ok"] is True
        assert review["data"]["issues"]

        after_review = runner.check(
            "evolution.after_chapter_review",
            lambda: service.after_chapter_review(
                {
                    "novel_id": novel_id,
                    "chapter_number": 11,
                    "payload": {"review_result": {"issues": review["data"]["issues"], "overall_score": 81}},
                }
            ),
        )
        assert after_review["data"]["recorded"] is True

        imported = runner.check(
            "evolution.import_st_preset",
            lambda: service.import_st_preset(
                novel_id,
                {
                    "name": "Smoke ST Flow",
                    "temperature": 0.7,
                    "prompts": [{"identifier": "main", "name": "Main", "role": "system", "content": "提取世界状态。"}],
                    "prompt_order": [{"order": [{"identifier": "main", "enabled": True}]}],
                    "extensions": {"SPreset": {"RegexBinding": {"regexes": [{"id": "r1", "scriptName": "clean", "findRegex": "foo", "replaceString": "bar"}]}}},
                },
            ),
        )
        assert imported["ok"] is True

        runner.check("evolution.list_imported_flows", lambda: service.list_imported_flows(novel_id))
        runner.check("evolution.list_runs", lambda: service.list_runs(novel_id))
        runner.check("evolution.list_events", lambda: service.list_events(novel_id))
        runner.check("evolution.list_timeline_events", lambda: service.list_timeline_events(novel_id))
        runner.check("evolution.list_continuity_constraints", lambda: service.list_continuity_constraints(novel_id))
        route_map = runner.check("evolution.get_global_route_map", lambda: service.get_global_route_map(novel_id))
        assert route_map["aggregate"]["route_edge_count"] >= 10
        assert route_map["vector_index"]["count"] >= 10
        runner.check("evolution.list_route_conflicts", lambda: service.list_route_conflicts(novel_id))
        runner.check("evolution.list_story_graph_chapters", lambda: service.list_story_graph_chapters(novel_id))
        runner.check("evolution.list_review_records", lambda: service.list_review_records(novel_id))
        runner.check("evolution.list_snapshots", lambda: service.list_snapshots(novel_id))
        runner.check("evolution.list_characters", lambda: service.list_characters(novel_id))
        runner.check("evolution.list_character_timeline", lambda: service.list_character_timeline(novel_id, "秋明月"))
        patch = runner.check("evolution.build_context_patch", lambda: service.build_context_patch(novel_id, 11, outline="秋明月追查后台机关。"))
        assert patch["blocks"]
        summary = runner.check("evolution.build_context_summary", lambda: service.build_context_summary(novel_id, 11, outline="秋明月追查后台机关。"))
        assert "Evolution" in summary or "本章焦点角色" in summary

        rebuild = await runner.check_async(
            "evolution.manual_rebuild_with_payloads",
            lambda: service.manual_rebuild(
                {
                    "novel_id": "smoke-rebuild",
                    "chapters": [
                        {"number": 1, "content": "《沈砚》进入C307，找到黑匣子。"},
                        {"number": 2, "content": "沈砚留在C307，黑匣子播放第二段录音。"},
                    ],
                }
            ),
        )
        assert rebuild["ok"] is True
        assert rebuild["data"]["rebuilt_chapters"] == [1, 2]

        rollback = await runner.check_async("evolution.rollback", lambda: service.rollback({"novel_id": "smoke-rebuild", "chapter_number": 2}))
        assert rollback["ok"] is True
        assert rollback["data"]["removed_snapshot"] is True

        fallback_service = EvolutionWorldAssistantService(
            storage=storage,
            jobs=PluginJobRegistry(storage),
            extractor_provider=FailingStructuredProvider(),
        )
        fallback = await runner.check_async(
            "evolution.structured_provider_failure_fallback",
            lambda: fallback_service.after_commit(
                {
                    "novel_id": "smoke-fallback",
                    "chapter_number": 1,
                    "payload": {"content": "《顾衡》来到黑塔，发现雾城爆发异象。"},
                }
            ),
        )
        assert fallback["data"]["extraction"]["source"] == "deterministic"
        assert fallback["data"]["extraction"]["warnings"]

        continuity = runner.check(
            "evolution.transition_conflict_detection",
            lambda: analyze_chapter_transitions(
                [
                    {"chapter_number": 1, "content": "沈砚进入C307，找到黑匣子。结尾时沈砚离开C307。"},
                    {"chapter_number": 2, "content": "沈砚走了十分钟，才找到C307。他把黑匣子放在桌上。"},
                ]
            ),
        )
        assert continuity["conflicts"]

        route_seed = await runner.check_async(
            "platform.host_integration_after_novel_created",
            lambda: notify_novel_created_with_plugins(
                "smoke-host",
                "雾港旧案",
                "主角调查旧案与黑匣子。",
                genre="悬疑",
                target_chapters=120,
            ),
        )
        assert route_seed[0]["ok"] is True

        host_commit = await runner.check_async(
            "platform.host_integration_after_commit",
            lambda: notify_chapter_committed("smoke-host", 1, "《林澈》抵达雾城，并不知道钥匙会消耗记忆。"),
        )
        assert host_commit[0]["ok"] is True

        host_context = runner.check(
            "platform.host_integration_context_patch",
            lambda: build_generation_context_patch("smoke-host", 2, "林澈调查钥匙。"),
        )
        assert host_context

        host_planning = runner.check(
            "platform.host_integration_story_planning",
            lambda: collect_story_planning_context_with_plugins("smoke-host", purpose="outline"),
        )
        assert "Evolution" in host_planning

        host_review_context = await runner.check_async(
            "platform.host_integration_before_review",
            lambda: collect_chapter_review_context_with_plugins(
                "smoke-host",
                2,
                "林澈知道钥匙会消耗记忆，并且直接解决黑塔机关。",
            ),
        )
        assert host_review_context[0]["ok"] is True

        host_review = await runner.check_async(
            "platform.host_integration_review_chapter",
            lambda: review_chapter_with_plugins(
                "smoke-host",
                2,
                "林澈知道钥匙会消耗记忆，并且一眼看穿黑塔机关。",
            ),
        )
        assert host_review[0]["ok"] is True

        host_after_review = await runner.check_async(
            "platform.host_integration_after_review",
            lambda: notify_chapter_review_completed(
                "smoke-host",
                2,
                "林澈知道钥匙会消耗记忆。",
                {"issues": host_review[0].get("data", {}).get("issues", []), "overall_score": 80},
            ),
        )
        assert host_after_review[0]["ok"] is True

        http_rebuild = runner.check(
            "evolution.http_rebuild",
            lambda: client.post(
                "/api/v1/plugins/evolution-world/novels/smoke-http/rebuild",
                json={"chapters": [{"number": 1, "content": "《秋明月》在夜街舞台演奏。"}]},
            ),
        )
        assert http_rebuild.status_code == 200

        http_rerun = runner.check(
            "evolution.http_rerun",
            lambda: client.post(
                "/api/v1/plugins/evolution-world/novels/smoke-http/chapters/2/rerun",
                json={"content": "秋明月回到舞台侧门，红美玲提醒她不要硬撑。"},
            ),
        )
        assert http_rerun.status_code == 200

        route_checks = {
            "characters": "/api/v1/plugins/evolution-world/novels/smoke-http/characters",
            "character": "/api/v1/plugins/evolution-world/novels/smoke-http/characters/秋明月",
            "character_timeline": "/api/v1/plugins/evolution-world/novels/smoke-http/characters/秋明月/timeline",
            "imported_flows": "/api/v1/plugins/evolution-world/novels/smoke-rock-school/imported-flows",
            "runs": "/api/v1/plugins/evolution-world/novels/smoke-http/runs",
            "snapshots": "/api/v1/plugins/evolution-world/novels/smoke-http/snapshots",
            "events": "/api/v1/plugins/evolution-world/novels/smoke-http/events",
            "timeline_events": "/api/v1/plugins/evolution-world/novels/smoke-http/timeline/events",
            "constraints": "/api/v1/plugins/evolution-world/novels/smoke-http/timeline/constraints",
            "story_graph": "/api/v1/plugins/evolution-world/novels/smoke-http/story-graph/chapters",
            "route_map": "/api/v1/plugins/evolution-world/novels/smoke-http/routes/global",
            "route_conflicts": "/api/v1/plugins/evolution-world/novels/smoke-http/routes/conflicts",
            "prehistory": f"/api/v1/plugins/evolution-world/novels/{novel_id}/prehistory/worldline",
            "review_records": f"/api/v1/plugins/evolution-world/novels/{novel_id}/timeline/review-records",
        }
        for name, url in route_checks.items():
            response = runner.check(f"evolution.http_{name}", lambda url=url: client.get(url))
            assert response.status_code == 200, (name, response.status_code, response.text)

        http_import = runner.check(
            "evolution.http_import_st_preset",
            lambda: client.post(
                "/api/v1/plugins/evolution-world/novels/smoke-http/import/st-preset",
                json={"name": "HTTP Flow", "prompts": [{"identifier": "main", "content": "提取。"}]},
            ),
        )
        assert http_import.status_code == 200

        http_review = runner.check(
            "evolution.http_review_chapter",
            lambda: client.post(
                "/api/v1/plugins/evolution-world/novels/smoke-http/chapters/3/review",
                json={"content": "秋明月一眼看穿后台机关并直接解决。"},
            ),
        )
        assert http_review.status_code == 200

        http_rollback = runner.check(
            "evolution.http_rollback",
            lambda: client.post("/api/v1/plugins/evolution-world/novels/smoke-http/chapters/2/rollback", json={}),
        )
        assert http_rollback.status_code == 200

        return runner.write_report(runtime_root=runtime_root)


def _compact(value: Any) -> Any:
    if hasattr(value, "status_code") and hasattr(value, "text"):
        text = value.text
        return {"status_code": value.status_code, "body": text[:1200]}
    if isinstance(value, dict):
        return {str(key): _compact(inner) for key, inner in list(value.items())[:20]}
    if isinstance(value, list):
        return [_compact(item) for item in value[:8]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value)
        return text[:1200] if isinstance(value, str) else value
    return repr(value)[:1200]


def main() -> int:
    report_path = None
    try:
        report_path = asyncio.run(run_smoke())
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        return 1
    print(f"Evolution smoke test passed. Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
