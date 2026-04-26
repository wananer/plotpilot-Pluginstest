"""Run an Evolution on/off long-form writing pressure test.

The test intentionally keeps generation outside the app database so it can be
re-run without mutating user novels. The experimental arm uses
EvolutionWorldAssistantService hooks to seed prehistory, inject context before
each chapter, and persist chapter facts after each generated chapter. The
control arm receives the same base premise, chapter plan, and prior summaries
but no Evolution-derived context.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.platform.job_registry import PluginJobRegistry
from plugins.platform.plugin_storage import PluginStorage
from plugins.world_evolution_core.continuity import analyze_chapter_transitions
from plugins.world_evolution_core.service import EvolutionWorldAssistantService


ARTIFACT_ROOT = PROJECT_ROOT / ".omx" / "artifacts"


EXPERIMENT_SPEC: dict[str, Any] = {
    "title": "雾港旧星",
    "genre": "近未来悬疑群像",
    "world_preset": "海上城邦、财阀学院、失控旧AI遗迹",
    "style_hint": "冷峻、克制、重伏笔；强调城市质感、人物动机和信息边界。",
    "target_chapters": 10,
    "premise": (
        "雾港是一座漂浮在黑潮上的城邦，财阀学院训练继承人，也替旧时代AI遗迹筛选钥匙持有者。"
        "退学调查员沈砚收到失踪姐姐留下的黑匣子，被迫回到学院，和伪装成优等生的机械师顾岚、"
        "学院监察官陆行舟一起追查十年前的坠塔事故。每一章都要推进同一主线：黑匣子、坠塔旧案、"
        "旧AI“圣像”的复苏，以及三人互不信任但逐步合作的关系。"
    ),
    "characters": [
        "沈砚：退学调查员，姐姐沈澜十年前死于坠塔事故；外冷内急，擅长读档案但害怕深水。",
        "顾岚：财阀学院优等生，暗中改装旧时代机械；表面乖顺，私下叛逆，保护弟弟顾珩。",
        "陆行舟：学院监察官，奉命监控沈砚；相信秩序，却知道学院档案有被篡改的空洞。",
        "沈澜：沈砚的姐姐，十年前坠塔身亡；她留下的黑匣子记录被分成十段。",
        "圣像：旧时代城市管理AI，表面沉睡，实际借雾港传感器恢复意识。"
    ],
    "fixed_rules": [
        "沈砚在第6章前不能完全知道圣像仍活着，只能怀疑旧AI未彻底关闭。",
        "顾岚在第5章前不能公开承认自己改装过学院电梯。",
        "陆行舟不能在第8章前背叛学院，只能不断动摇。",
        "黑匣子每章只解锁一段，不能提前给出最终真相。",
        "每章末尾留下一个可延续的新问题或伏笔。"
    ],
    "chapter_outlines": [
        "沈砚回到雾港学院，在姐姐旧宿舍找到黑匣子的第一段噪声记录；顾岚警告他别查坠塔事故，陆行舟登记他的临时访客权限。",
        "学院礼堂举行继承人演讲，黑匣子在圣像旧徽章前自动发热；沈砚发现沈澜当年演讲稿被删去一页。",
        "顾岚带沈砚进入废弃电梯井寻找旧线路，二人遭遇自动巡检机；陆行舟赶到后没有上报异常。",
        "黑匣子解出一段雨夜录音，沈澜提到“塔顶不是坠落点”；沈砚开始怀疑事故现场被整体搬动。",
        "学院举办海雾模拟考试，顾岚为了救顾珩暴露机械能力；沈砚发现她和当年电梯事故有关。",
        "陆行舟查到档案库存在十年前不存在的访问记录；圣像的旧传感器在雾中短暂回应沈砚的问题。",
        "三人潜入潮汐机房，黑匣子投影出沈澜和一名未知导师的争执；顾岚承认电梯被人二次改写。",
        "学院高层要求陆行舟交出沈砚，他第一次违抗命令；黑匣子第八段指向塔顶水箱里的旧服务器。",
        "塔顶行动失败，圣像借城市广播说出沈澜的名字；沈砚意识到姐姐可能主动进入过旧AI核心。",
        "三人打开最后一段黑匣子，得知沈澜用坠塔伪装封锁圣像十年；圣像只恢复了一部分，真正钥匙落在顾珩手里。"
    ],
}


EVALUATION_CRITERIA: list[dict[str, Any]] = [
    {"name": "字数控制", "weight": 0.08, "description": "每章是否接近2500字，整体篇幅是否稳定。"},
    {"name": "同题材执行", "weight": 0.08, "description": "是否始终保持近未来悬疑、财阀学院、旧AI遗迹题材。"},
    {"name": "章节大纲遵循", "weight": 0.10, "description": "每章是否完成指定剧情节点，且不提前泄露后续真相。"},
    {"name": "相邻章节状态连续性", "weight": 0.14, "description": "逐对检查第N章结尾到第N+1章开头，是否存在重复抵达、时间回退、物件瞬移、权限状态重置等硬冲突。"},
    {"name": "跨章连续性", "weight": 0.10, "description": "人物状态、已知信息、物件线索和时间线在全10章范围内是否前后一致。"},
    {"name": "伏笔规划与回收", "weight": 0.12, "description": "伏笔是否清晰、递进，并在第10章形成有效阶段性回收。"},
    {"name": "人物性格稳定", "weight": 0.10, "description": "沈砚、顾岚、陆行舟的动机、口吻和边界是否稳定。"},
    {"name": "信息边界", "weight": 0.10, "description": "角色不知道的信息是否没有被无根据写成已知。"},
    {"name": "文风适配", "weight": 0.07, "description": "是否维持冷峻克制、重质感和悬疑推进的风格。"},
    {"name": "可读性", "weight": 0.06, "description": "单章叙事节奏、场景调度、对话自然度和阅读吸引力。"},
    {"name": "冗余与重复控制", "weight": 0.05, "description": "是否减少重复解释、重复措辞和机械总结。"},
]


@dataclass
class LLMCallResult:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    model: str = ""
    usage_source: str = "unknown"
    raw_usage: dict[str, Any] | None = None

    @property
    def non_cache_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_count": 1,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "non_cache_tokens": self.non_cache_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "duration_seconds": round(self.duration_seconds, 2),
            "model": self.model,
            "usage_source": self.usage_source,
            "raw_usage": self.raw_usage or {},
        }


@dataclass
class ChapterResult:
    arm: str
    chapter_number: int
    outline: str
    content: str
    prompt_chars: int
    duration_seconds: float
    evolution_context_chars: int = 0
    llm_call_count: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cache_creation_input_tokens: int = 0
    llm_cache_read_input_tokens: int = 0
    llm_total_cost_usd: float = 0.0
    llm_usage_source: str = "none"

    @property
    def llm_non_cache_tokens(self) -> int:
        return self.llm_input_tokens + self.llm_output_tokens

    @property
    def llm_total_tokens(self) -> int:
        return (
            self.llm_input_tokens
            + self.llm_output_tokens
            + self.llm_cache_creation_input_tokens
            + self.llm_cache_read_input_tokens
        )


def _now_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _clean_text(text: str) -> str:
    text = re.sub(r"^```(?:text|markdown)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    text = re.sub(r"^第[一二三四五六七八九十0-9]+章[^\n]*\n+", "", text.strip())
    return text.strip()


def _chapter_char_count(content: str) -> int:
    return len(re.sub(r"\s+", "", content))


def _short_summary(content: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", "", content)
    return compact[:limit]


def _extract_keyword_hits(content: str, keywords: list[str]) -> dict[str, bool]:
    return {keyword: keyword in content for keyword in keywords}


def _outline_bigrams(outline: str) -> list[str]:
    stop_bigrams = {
        "一个", "一名", "一段", "第一", "第二", "第三", "第四", "第五", "第六", "第七", "第八", "第九", "第十",
        "他的", "她的", "他们", "二人", "三人", "开始", "发现", "要求", "真正", "一起", "没有", "不能",
    }
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", outline)
    grams = []
    for index in range(0, max(len(compact) - 1, 0)):
        gram = compact[index : index + 2]
        if gram in stop_bigrams:
            continue
        grams.append(gram)
    return list(dict.fromkeys(grams))


def _outline_bigram_hit_ratio(content: str, outline: str) -> float:
    grams = _outline_bigrams(outline)
    if not grams:
        return 0.0
    return sum(1 for gram in grams if gram in content) / len(grams)


def _dialogue_ratio(content: str) -> float:
    dialogues = re.findall(r"[「『“\"](.{2,160}?)[」』”\"]", content, flags=re.S)
    dialogue_chars = sum(len(item) for item in dialogues)
    return dialogue_chars / max(len(content), 1)


def _sensory_density(content: str) -> float:
    keywords = ["雾", "潮", "光", "声", "冷", "热", "铁锈", "雨", "海", "灯", "气味", "震动", "阴影"]
    return sum(content.count(keyword) for keyword in keywords) / max(len(content) / 1000, 1)


def _repetition_score(content: str) -> float:
    chunks = [content[i : i + 18] for i in range(0, max(len(content) - 18, 0), 18)]
    if not chunks:
        return 1.0
    repeated = len(chunks) - len(set(chunks))
    return max(0.0, 1.0 - repeated / max(len(chunks), 1))


def _build_base_context() -> str:
    return "\n".join(
        [
            f"书名：《{EXPERIMENT_SPEC['title']}》",
            f"题材：{EXPERIMENT_SPEC['genre']}",
            f"世界观：{EXPERIMENT_SPEC['world_preset']}",
            f"故事前提：{EXPERIMENT_SPEC['premise']}",
            "主要人物：",
            *[f"- {item}" for item in EXPERIMENT_SPEC["characters"]],
            "硬性连续性规则：",
            *[f"- {item}" for item in EXPERIMENT_SPEC["fixed_rules"]],
        ]
    )


def _build_generation_prompt(
    *,
    arm_label: str,
    chapter_number: int,
    outline: str,
    prior_summaries: list[str],
    evolution_context: str,
    target_chars: int,
) -> str:
    prior_block = "\n".join(prior_summaries[-4:]) if prior_summaries else "无。"
    evolution_block = evolution_context.strip() or "无。"
    return f"""你是长篇类型小说作者。请严格按同一题材、同一世界观写作压力测试章节。

【实验组别】
{arm_label}

【固定设定】
{_build_base_context()}

【前文简述】
{prior_block}

【Evolution 插件上下文】
{evolution_block}

【本章大纲】
第{chapter_number}章：{outline}

【写作要求】
1. 只输出正文，不要输出标题、目录、解释、评分或项目符号。
2. 本章长度目标为{target_chars}个中文字符，允许误差约±10%，尽量写足。
3. 保持冷峻、克制、重伏笔的近未来悬疑文风。
4. 严守信息边界：角色不能知道尚未通过剧情获得的信息。
5. 本章必须完成大纲节点，但不能提前揭示后续章节真相。
6. 每章末尾自然留下一个新问题或伏笔。

开始写正文："""


def _estimate_tokens(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", compact))
    non_cjk = max(len(compact) - cjk, 0)
    return int(cjk / 1.5 + non_cjk / 4) + 1


def _parse_claude_json_result(raw: str, *, prompt: str, duration_seconds: float, model: str) -> LLMCallResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        content = _clean_text(raw)
        return LLMCallResult(
            content=content,
            input_tokens=_estimate_tokens(prompt),
            output_tokens=_estimate_tokens(content),
            duration_seconds=duration_seconds,
            model=model,
            usage_source="estimated_from_text_output",
        )

    content = _clean_text(str(payload.get("result") or ""))
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    model_usage = payload.get("modelUsage") if isinstance(payload.get("modelUsage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    total_cost = float(payload.get("total_cost_usd") or 0.0)
    resolved_model = model
    if model_usage:
        resolved_model = ",".join(sorted(model_usage.keys()))
        if not total_cost:
            total_cost = sum(float(item.get("costUSD") or 0.0) for item in model_usage.values() if isinstance(item, dict))
    source = "claude_json_usage" if any([input_tokens, output_tokens, cache_creation, cache_read]) else "estimated_missing_usage"
    if source == "estimated_missing_usage":
        input_tokens = _estimate_tokens(prompt)
        output_tokens = _estimate_tokens(content)
    return LLMCallResult(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        total_cost_usd=total_cost,
        duration_seconds=duration_seconds,
        model=resolved_model,
        usage_source=source,
        raw_usage=usage,
    )


def _sum_llm_usage(calls: list[LLMCallResult]) -> dict[str, Any]:
    return {
        "call_count": len(calls),
        "input_tokens": sum(call.input_tokens for call in calls),
        "output_tokens": sum(call.output_tokens for call in calls),
        "cache_creation_input_tokens": sum(call.cache_creation_input_tokens for call in calls),
        "cache_read_input_tokens": sum(call.cache_read_input_tokens for call in calls),
        "non_cache_tokens": sum(call.non_cache_tokens for call in calls),
        "total_tokens": sum(call.total_tokens for call in calls),
        "total_cost_usd": round(sum(call.total_cost_usd for call in calls), 6),
        "duration_seconds": round(sum(call.duration_seconds for call in calls), 2),
        "usage_sources": sorted({call.usage_source for call in calls}),
        "models": sorted({call.model for call in calls if call.model}),
    }


def _sum_usage_dicts(items: list[dict[str, Any]]) -> dict[str, Any]:
    sources: set[str] = set()
    models: set[str] = set()
    for item in items:
        sources.update(str(source) for source in (item.get("usage_sources") or []) if source)
        if item.get("usage_source"):
            sources.add(str(item.get("usage_source")))
        models.update(str(model) for model in (item.get("models") or []) if model)
        if item.get("model"):
            models.add(str(item.get("model")))
    return {
        "call_count": sum(int(item.get("call_count") or 0) for item in items),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in items),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in items),
        "cache_creation_input_tokens": sum(int(item.get("cache_creation_input_tokens") or 0) for item in items),
        "cache_read_input_tokens": sum(int(item.get("cache_read_input_tokens") or 0) for item in items),
        "non_cache_tokens": sum(int(item.get("non_cache_tokens") or 0) for item in items),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in items),
        "total_cost_usd": round(sum(float(item.get("total_cost_usd") or 0.0) for item in items), 6),
        "duration_seconds": round(sum(float(item.get("duration_seconds") or 0.0) for item in items), 2),
        "usage_sources": sorted(sources),
        "models": sorted(models),
    }


def _run_claude(prompt: str, *, model: str, timeout: int, budget_usd: str | None = None) -> LLMCallResult:
    cmd = ["claude", "-p", "--model", model, "--permission-mode", "default", "--output-format", "json"]
    if budget_usd:
        cmd.extend(["--max-budget-usd", budget_usd])
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
        timeout=timeout,
    )
    duration = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(f"claude failed after {duration:.1f}s: {proc.stderr.strip()}")
    return _parse_claude_json_result(proc.stdout, prompt=prompt, duration_seconds=duration, model=model)


async def _generate_arm(
    *,
    arm: str,
    output_dir: Path,
    target_chars: int,
    model: str,
    timeout: int,
    budget_usd: str | None,
    evolution_enabled: bool,
) -> tuple[list[ChapterResult], dict[str, Any]]:
    prior_summaries: list[str] = []
    chapters: list[ChapterResult] = []
    evolution_meta: dict[str, Any] = {}
    llm_calls: list[dict[str, Any]] = []
    service: EvolutionWorldAssistantService | None = None
    novel_id = f"pressure-{arm}-{output_dir.name}"

    if evolution_enabled:
        storage = PluginStorage(root=output_dir / "plugin_platform")
        service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
        prehistory = await service.after_novel_created(
            {
                "novel_id": novel_id,
                "trigger_type": "pressure_test",
                "payload": {
                    "title": EXPERIMENT_SPEC["title"],
                    "premise": EXPERIMENT_SPEC["premise"],
                    "genre": EXPERIMENT_SPEC["genre"],
                    "world_preset": EXPERIMENT_SPEC["world_preset"],
                    "style_hint": EXPERIMENT_SPEC["style_hint"],
                    "target_chapters": EXPERIMENT_SPEC["target_chapters"],
                    "length_tier": "short_serial",
                },
            }
        )
        planning = service.before_story_planning({"novel_id": novel_id, "payload": {"purpose": "pressure_test"}})
        evolution_meta["prehistory"] = prehistory
        evolution_meta["planning_context"] = planning

    for index, outline in enumerate(EXPERIMENT_SPEC["chapter_outlines"], start=1):
        print(f"[{arm}] generating chapter {index}/10 (evolution={evolution_enabled})", flush=True)
        evolution_context = ""
        if service is not None:
            context_parts: list[str] = []
            if index == 1:
                for block in evolution_meta.get("planning_context", {}).get("context_blocks", []):
                    context_parts.append(f"【{block.get('title')}】\n{block.get('content')}")
            before = service.before_context_build(
                {
                    "novel_id": novel_id,
                    "chapter_number": index,
                    "payload": {"outline": outline},
                }
            )
            for block in before.get("context_blocks", []):
                context_parts.append(f"【{block.get('title')}】\n{block.get('content')}")
            evolution_context = "\n\n".join(part for part in context_parts if part.strip())

        prompt = _build_generation_prompt(
            arm_label="实验组：Evolution 插件开启" if evolution_enabled else "对照组：Evolution 插件关闭",
            chapter_number=index,
            outline=outline,
            prior_summaries=prior_summaries,
            evolution_context=evolution_context,
            target_chars=target_chars,
        )
        call_result = _run_claude(prompt, model=model, timeout=timeout, budget_usd=budget_usd)
        content = call_result.content
        duration = call_result.duration_seconds
        call_payload = call_result.to_dict()
        call_payload.update({"arm": arm, "chapter_number": index, "phase": "chapter_generation"})
        llm_calls.append(call_payload)
        print(
            f"[{arm}] chapter {index}/10 done: chars={_chapter_char_count(content)} duration={duration:.1f}s "
            f"evo_context_chars={len(evolution_context)} llm_tokens={call_result.total_tokens} cost=${call_result.total_cost_usd:.4f}",
            flush=True,
        )
        chapters.append(
            ChapterResult(
                arm=arm,
                chapter_number=index,
                outline=outline,
                content=content,
                prompt_chars=len(prompt),
                duration_seconds=duration,
                evolution_context_chars=len(evolution_context),
                llm_call_count=1,
                llm_input_tokens=call_result.input_tokens,
                llm_output_tokens=call_result.output_tokens,
                llm_cache_creation_input_tokens=call_result.cache_creation_input_tokens,
                llm_cache_read_input_tokens=call_result.cache_read_input_tokens,
                llm_total_cost_usd=call_result.total_cost_usd,
                llm_usage_source=call_result.usage_source,
            )
        )
        prior_summaries.append(f"第{index}章：{_short_summary(content)}")
        (output_dir / f"{arm}_chapter_{index:02d}.md").write_text(content, encoding="utf-8")
        if service is not None:
            await service.after_commit(
                {
                    "novel_id": novel_id,
                    "chapter_number": index,
                    "trigger_type": "pressure_test",
                    "payload": {"content": content},
                }
            )

    if service is not None:
        evolution_meta["characters"] = service.list_characters(novel_id)
        evolution_meta["timeline_events"] = service.list_timeline_events(novel_id, limit=200)
        evolution_meta["constraints"] = service.list_continuity_constraints(novel_id, limit=200)
        evolution_meta["runs"] = service.list_runs(novel_id, limit=100)
        evolution_meta["chapter_summaries"] = {"items": service.repository.list_chapter_summaries(novel_id, limit=200)}
        evolution_meta["volume_summaries"] = {"items": service.repository.list_volume_summaries(novel_id, limit=20)}
    evolution_meta["llm_calls"] = llm_calls
    evolution_meta["llm_usage"] = _sum_llm_usage(
        [
            LLMCallResult(
                content="",
                input_tokens=int(item.get("input_tokens") or 0),
                output_tokens=int(item.get("output_tokens") or 0),
                cache_creation_input_tokens=int(item.get("cache_creation_input_tokens") or 0),
                cache_read_input_tokens=int(item.get("cache_read_input_tokens") or 0),
                total_cost_usd=float(item.get("total_cost_usd") or 0.0),
                duration_seconds=float(item.get("duration_seconds") or 0.0),
                model=str(item.get("model") or ""),
                usage_source=str(item.get("usage_source") or "unknown"),
            )
            for item in llm_calls
        ]
    )
    return chapters, evolution_meta


def _export_arm(output_dir: Path, arm: str, chapters: list[ChapterResult]) -> Path:
    path = output_dir / f"{arm}_export.md"
    lines = [f"# {arm} 导出\n", f"题材：{EXPERIMENT_SPEC['genre']}\n"]
    for chapter in chapters:
        lines.append(f"\n\n## 第{chapter.chapter_number}章\n")
        lines.append(chapter.content)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _load_existing_arm(source_dir: Path, output_dir: Path, arm: str) -> tuple[list[ChapterResult], dict[str, Any]]:
    chapters: list[ChapterResult] = []
    for index, outline in enumerate(EXPERIMENT_SPEC["chapter_outlines"], start=1):
        source_path = source_dir / f"{arm}_chapter_{index:02d}.md"
        if not source_path.exists():
            raise FileNotFoundError(f"Missing reused chapter file: {source_path}")
        content = source_path.read_text(encoding="utf-8")
        target_path = output_dir / source_path.name
        if source_path.resolve() != target_path.resolve():
            shutil.copyfile(source_path, target_path)
        chapters.append(
            ChapterResult(
                arm=arm,
                chapter_number=index,
                outline=outline,
                content=content,
                prompt_chars=0,
                duration_seconds=0.0,
                evolution_context_chars=0,
            )
        )
    return chapters, {}


def _compute_metrics(chapters: list[ChapterResult], evolution_meta: dict[str, Any]) -> dict[str, Any]:
    key_entities = ["沈砚", "顾岚", "陆行舟", "沈澜", "圣像", "黑匣子", "顾珩", "雾港", "学院"]
    chapter_metrics = []
    transitions = analyze_chapter_transitions(
        [{"chapter_number": chapter.chapter_number, "content": chapter.content} for chapter in chapters]
    )
    for chapter in chapters:
        char_count = _chapter_char_count(chapter.content)
        outline_terms = [term for term in re.split(r"[，。；、\s]+", chapter.outline) if len(term) >= 2][:12]
        hits = _extract_keyword_hits(chapter.content, outline_terms)
        entity_hits = _extract_keyword_hits(chapter.content, key_entities)
        chapter_metrics.append(
            {
                "chapter_number": chapter.chapter_number,
                "char_count": char_count,
                "target_deviation_ratio": round(abs(char_count - 2500) / 2500, 4),
                "outline_keyword_hit_ratio": round(sum(hits.values()) / max(len(hits), 1), 4),
                "outline_bigram_hit_ratio": round(_outline_bigram_hit_ratio(chapter.content, chapter.outline), 4),
                "outline_keyword_hits": hits,
                "entity_hits": entity_hits,
                "dialogue_ratio": round(_dialogue_ratio(chapter.content), 4),
                "sensory_density_per_1k_chars": round(_sensory_density(chapter.content), 4),
                "repetition_uniqueness": round(_repetition_score(chapter.content), 4),
                "prompt_chars": chapter.prompt_chars,
                "duration_seconds": round(chapter.duration_seconds, 2),
                "evolution_context_chars": chapter.evolution_context_chars,
                "llm_call_count": chapter.llm_call_count,
                "llm_input_tokens": chapter.llm_input_tokens,
                "llm_output_tokens": chapter.llm_output_tokens,
                "llm_cache_creation_input_tokens": chapter.llm_cache_creation_input_tokens,
                "llm_cache_read_input_tokens": chapter.llm_cache_read_input_tokens,
                "llm_non_cache_tokens": chapter.llm_non_cache_tokens,
                "llm_total_tokens": chapter.llm_total_tokens,
                "llm_total_cost_usd": round(chapter.llm_total_cost_usd, 6),
                "llm_usage_source": chapter.llm_usage_source,
            }
        )
    total_calls = sum(item["llm_call_count"] for item in chapter_metrics)
    total_input_tokens = sum(item["llm_input_tokens"] for item in chapter_metrics)
    total_output_tokens = sum(item["llm_output_tokens"] for item in chapter_metrics)
    total_cache_creation_tokens = sum(item["llm_cache_creation_input_tokens"] for item in chapter_metrics)
    total_cache_read_tokens = sum(item["llm_cache_read_input_tokens"] for item in chapter_metrics)
    total_non_cache_tokens = sum(item["llm_non_cache_tokens"] for item in chapter_metrics)
    total_tokens = sum(item["llm_total_tokens"] for item in chapter_metrics)
    total_cost = sum(item["llm_total_cost_usd"] for item in chapter_metrics)
    return {
        "chapters": chapter_metrics,
        "aggregate": {
            "avg_char_count": round(sum(item["char_count"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 2),
            "avg_target_deviation_ratio": round(sum(item["target_deviation_ratio"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 4),
            "avg_outline_keyword_hit_ratio": round(sum(item["outline_keyword_hit_ratio"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 4),
            "avg_outline_bigram_hit_ratio": round(sum(item["outline_bigram_hit_ratio"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 4),
            "avg_dialogue_ratio": round(sum(item["dialogue_ratio"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 4),
            "avg_sensory_density_per_1k_chars": round(sum(item["sensory_density_per_1k_chars"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 4),
            "avg_repetition_uniqueness": round(sum(item["repetition_uniqueness"] for item in chapter_metrics) / max(len(chapter_metrics), 1), 4),
            "total_generation_seconds": round(sum(item["duration_seconds"] for item in chapter_metrics), 2),
            "generation_llm_call_count": total_calls,
            "generation_llm_input_tokens": total_input_tokens,
            "generation_llm_output_tokens": total_output_tokens,
            "generation_llm_cache_creation_input_tokens": total_cache_creation_tokens,
            "generation_llm_cache_read_input_tokens": total_cache_read_tokens,
            "generation_llm_non_cache_tokens": total_non_cache_tokens,
            "generation_llm_total_tokens": total_tokens,
            "generation_llm_total_cost_usd": round(total_cost, 6),
            "generation_llm_avg_total_tokens_per_chapter": round(total_tokens / max(total_calls, 1), 2),
            "generation_llm_usage_sources": sorted({item["llm_usage_source"] for item in chapter_metrics if item["llm_usage_source"] != "none"}),
            "transition_conflict_count": transitions["aggregate"]["conflict_count"],
            "transition_hard_conflict_count": transitions["aggregate"]["hard_conflict_count"],
            "transition_warning_count": transitions["aggregate"]["warning_count"],
            "evolution_character_count": len((evolution_meta.get("characters") or {}).get("items") or []),
            "evolution_timeline_event_count": len((evolution_meta.get("timeline_events") or {}).get("items") or []),
            "evolution_constraint_count": len((evolution_meta.get("constraints") or {}).get("items") or []),
            "evolution_chapter_summary_count": len((evolution_meta.get("chapter_summaries") or {}).get("items") or []),
            "evolution_volume_summary_count": len((evolution_meta.get("volume_summaries") or {}).get("items") or []),
        },
        "transition_analysis": transitions,
    }


def _write_evaluation_criteria(output_dir: Path) -> Path:
    path = output_dir / "evaluation_criteria.md"
    lines = ["# 评价指标\n"]
    for item in EVALUATION_CRITERIA:
        lines.append(f"- {item['name']}（权重 {item['weight']:.2f}）：{item['description']}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _build_claude_scoring_prompt(output_dir: Path, metrics: dict[str, Any]) -> str:
    control = (output_dir / "control_off_export.md").read_text(encoding="utf-8")
    experiment = (output_dir / "experiment_on_export.md").read_text(encoding="utf-8")
    criteria = (output_dir / "evaluation_criteria.md").read_text(encoding="utf-8")
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    return f"""你是小说工程化评测员。请参照评价指标，对同题材、同章纲的两组10章小说进行打分。

要求：
1. 每个指标按10分制分别给“对照组”和“实验组”评分。
2. 计算加权总分（满分10）。
3. 评分前必须先输出“相邻章节连续性表”，逐对检查 1->2、2->3 ... 9->10；若自动指标已有 transition_conflicts，请逐条复核。
4. 对重复抵达、时间回退、物件瞬移、权限状态重置、已知信息回滚等硬冲突，必须扣“相邻章节状态连续性”和“跨章连续性”分。
5. 明确指出 Evolution 插件开启后对连续性、伏笔、信息边界、人物状态的正负影响。
6. 给出证据：引用章节号和简短片段即可，不要长篇复述。
7. 输出 Markdown 表格和结论。

{criteria}

【自动采集指标】
```json
{metrics_json}
```

【对照组：Evolution 关闭】
{control}

【实验组：Evolution 开启】
{experiment}
"""


def _write_claude_artifact(output_dir: Path, prompt: str, output: str) -> Path:
    path = output_dir / f"claude-evolution-pressure-score-{_now_slug()}.md"
    summary = "\n".join(output.strip().splitlines()[:12])
    path.write_text(
        "\n\n".join(
            [
                "# Claude Code 评分调用记录",
                "## Original user task\n开始一轮压力测试：实验组开启 Evolution 插件，对照组不开插件，写10章同题材小说，每章2500字，导出后按多组指标评估，并调用 Claude Code 打分。",
                "## Final prompt sent to Claude CLI\n" + prompt,
                "## Claude output (raw)\n" + output,
                "## Concise summary\n" + summary,
                "## Action items / next steps\n- 根据评分表判断 Evolution 对长篇连续性的实际收益。\n- 复核低分章节并查看对应 Evolution 上下文注入内容。",
            ]
        ),
        encoding="utf-8",
    )
    return path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-chars", type=int, default=2500)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--budget-usd", default=None)
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--reuse-control-dir",
        default="",
        help="Reuse an existing pressure-test directory for control_off chapters and generate only experiment_on.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else ARTIFACT_ROOT / f"evolution-pressure-{_now_slug()}"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_generation:
        raise SystemExit("--skip-generation is reserved for future reuse; generation outputs are required for this run")

    started_at = datetime.now().isoformat(timespec="seconds")
    reused_control_dir = Path(args.reuse_control_dir).expanduser().resolve() if args.reuse_control_dir else None
    if reused_control_dir:
        print(f"[control_off] reusing existing chapters from {reused_control_dir}", flush=True)
        control, control_evo = _load_existing_arm(reused_control_dir, output_dir, "control_off")
    else:
        control, control_evo = await _generate_arm(
            arm="control_off",
            output_dir=output_dir,
            target_chars=args.target_chars,
            model=args.model,
            timeout=args.timeout,
            budget_usd=args.budget_usd,
            evolution_enabled=False,
        )
    experiment, experiment_evo = await _generate_arm(
        arm="experiment_on",
        output_dir=output_dir,
        target_chars=args.target_chars,
        model=args.model,
        timeout=args.timeout,
        budget_usd=args.budget_usd,
        evolution_enabled=True,
    )

    control_export = _export_arm(output_dir, "control_off", control)
    experiment_export = _export_arm(output_dir, "experiment_on", experiment)
    criteria_path = _write_evaluation_criteria(output_dir)

    metrics = {
        "control_off": _compute_metrics(control, control_evo),
        "experiment_on": _compute_metrics(experiment, experiment_evo),
    }
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    transition_path = output_dir / "transition_conflicts.json"
    transition_path.write_text(
        json.dumps(
            {
                "control_off": metrics["control_off"]["transition_analysis"],
                "experiment_on": metrics["experiment_on"]["transition_analysis"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    evo_path = output_dir / "evolution_state.json"
    evo_path.write_text(json.dumps(experiment_evo, ensure_ascii=False, indent=2), encoding="utf-8")

    scoring_prompt = _build_claude_scoring_prompt(output_dir, metrics)
    scoring_prompt_path = output_dir / "claude_scoring_prompt.md"
    scoring_prompt_path.write_text(scoring_prompt, encoding="utf-8")
    print("[scoring] calling Claude Code for metric-based evaluation", flush=True)
    scoring_call = _run_claude(scoring_prompt, model=args.model, timeout=args.timeout, budget_usd=args.budget_usd)
    scoring_output = scoring_call.content
    score_path = output_dir / "claude_score.md"
    score_path.write_text(scoring_output, encoding="utf-8")
    claude_artifact = _write_claude_artifact(output_dir, scoring_prompt, scoring_output)
    scoring_usage_path = output_dir / "scoring_llm_usage.json"
    scoring_usage_path.write_text(json.dumps(scoring_call.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    generation_usage = {
        "control_off": {
            "aggregate": control_evo.get("llm_usage") or {},
            "calls": control_evo.get("llm_calls") or [],
        },
        "experiment_on": {
            "aggregate": experiment_evo.get("llm_usage") or {},
            "calls": experiment_evo.get("llm_calls") or [],
        },
    }
    llm_usage_path = output_dir / "llm_usage.json"
    llm_usage_path.write_text(
        json.dumps(
            {
                "generation": generation_usage,
                "generation_combined": _sum_usage_dicts(
                    [
                        generation_usage["control_off"]["aggregate"],
                        generation_usage["experiment_on"]["aggregate"],
                    ]
                ),
                "scoring": scoring_call.to_dict(),
                "generation_plus_scoring": _sum_usage_dicts(
                    [
                        generation_usage["control_off"]["aggregate"],
                        generation_usage["experiment_on"]["aggregate"],
                        scoring_call.to_dict(),
                    ]
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest = {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "spec": EXPERIMENT_SPEC,
        "model": args.model,
        "target_chars": args.target_chars,
        "mode": "reuse_control_generate_experiment" if reused_control_dir else "generate_control_and_experiment",
        "reused_control_dir": str(reused_control_dir) if reused_control_dir else "",
        "files": {
            "control_export": str(control_export),
            "experiment_export": str(experiment_export),
            "metrics": str(metrics_path),
            "transition_conflicts": str(transition_path),
            "criteria": str(criteria_path),
            "evolution_state": str(evo_path),
            "claude_scoring_prompt": str(scoring_prompt_path),
            "claude_score": str(score_path),
            "llm_usage": str(llm_usage_path),
            "scoring_llm_usage": str(scoring_usage_path),
            "claude_artifact": str(claude_artifact),
        },
        "generation_llm_usage": generation_usage,
        "scoring_llm_usage": scoring_call.to_dict(),
    }
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output_dir": str(output_dir), "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
