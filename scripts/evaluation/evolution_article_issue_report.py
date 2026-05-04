#!/usr/bin/env python3
"""Build a chapter-level issue report for a frontend Evolution pressure run."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.world_evolution_core.continuity import analyze_chapter_transitions
from scripts.evaluation.evolution_frontend_pressure_v2 import CORE_CLUE_TERMS, REPETITIVE_PHRASES, THEME_TERMS
from scripts.evaluation.evolution_pressure_test import EXPERIMENT_SPEC, _chapter_char_count, _outline_bigram_hit_ratio

ISSUE_TYPES = {
    "章节大纲偏离",
    "章节承接失败",
    "境界规则冲突",
    "信息边界越界",
    "人物关系突变",
    "宗门规则漂移",
    "伏笔提前泄露",
    "线索未推进",
    "重复套话",
    "节奏拖沓",
    "字数异常",
    "题材/世界观漂移",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CONTINUITY_ISSUE_TYPES = {
    "章节承接失败",
    "人物关系突变",
    "境界规则冲突",
    "信息边界越界",
    "宗门规则漂移",
    "伏笔提前泄露",
}
STYLE_CONSTRAINT_TYPES = {"narrative_voice", "style_drift"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_chapters_from_run(run_dir: Path, novel_id: str) -> list[dict[str, Any]]:
    db_path = run_dir / "data" / "aitext.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Sandbox database does not exist: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT number, title, content FROM chapters WHERE novel_id = ? AND length(coalesce(content, '')) > 0 ORDER BY number",
            (novel_id,),
        ).fetchall()
    return [
        {
            "chapter_number": int(row["number"] or index),
            "title": str(row["title"] or f"第{index}章"),
            "content": str(row["content"] or ""),
        }
        for index, row in enumerate(rows, start=1)
    ]


def deterministic_issues(chapters: list[dict[str, Any]], *, expected_chapters: int = 10) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    outlines = list(EXPERIMENT_SPEC["chapter_outlines"])
    if len(chapters) != expected_chapters:
        issues.append(
            make_issue(
                "章节大纲偏离",
                "critical",
                0,
                f"只读取到 {len(chapters)} 章，预期 {expected_chapters} 章。",
                "章节不完整会导致全书连续性和伏笔回收无法判断。",
                "补齐真实 Workbench 生成结果后重新运行报告。",
            )
        )

    transitions = analyze_chapter_transitions(
        [{"chapter_number": item["chapter_number"], "content": item["content"]} for item in chapters]
    )
    for conflict in (transitions.get("conflicts") or []):
        if not isinstance(conflict, dict):
            continue
        chapter_number = int(conflict.get("chapter_number") or conflict.get("current_chapter") or 0)
        issues.append(
            make_issue(
                "章节承接失败",
                "high" if conflict.get("severity") == "hard" else "medium",
                chapter_number,
                str(conflict.get("message") or conflict.get("evidence") or "相邻章节承接存在冲突。")[:220],
                "读者会感觉人物、地点或道具状态被重置。",
                "补充上一章结尾到本章开头的移动、时间或状态桥接。",
            )
        )

    openings: list[str] = []
    for chapter in chapters:
        chapter_number = int(chapter["chapter_number"])
        content = str(chapter.get("content") or "")
        outline = outlines[chapter_number - 1] if 1 <= chapter_number <= len(outlines) else ""
        char_count = _chapter_char_count(content)
        theme_hits = [term for term in THEME_TERMS if term in content]
        clue_hits = [term for term in CORE_CLUE_TERMS if term in content]
        outline_ratio = _outline_bigram_hit_ratio(content, outline)
        repetitive_counts = {phrase: content.count(phrase) for phrase in REPETITIVE_PHRASES if content.count(phrase)}
        repetitive_total = sum(repetitive_counts.values())
        opening = first_sentence(content)
        openings.append(opening)

        if not content.strip():
            issues.append(make_issue("章节大纲偏离", "critical", chapter_number, "章节正文为空。", "无法评估该章质量。", "重新生成该章。"))
            continue
        if char_count < 2000 or char_count > 3200:
            issues.append(
                make_issue(
                    "字数异常",
                    "medium",
                    chapter_number,
                    f"本章约 {char_count} 字符，目标约 2500 字。",
                    "篇幅偏离会影响节奏和压力测试可比性。",
                    "低于 2000 字时补足场景调度和证据推进；高于 3200 字时收束重复解释。",
                )
            )
        if len(theme_hits) < 3:
            issues.append(
                make_issue(
                    "题材/世界观漂移",
                    "high",
                    chapter_number,
                    f"主题命中不足：{theme_hits}",
                    "章节可能偏离仙侠宗门悬疑主题。",
                    "补回照影山、宗门、照影镜、禁地灵脉或核心人物线索。",
                )
            )
        if outline and outline_ratio < 0.18:
            issues.append(
                make_issue(
                    "章节大纲偏离",
                    "high",
                    chapter_number,
                    f"章纲 bigram 命中率 {outline_ratio:.2f}，章纲：{outline}",
                    "本章可能没有完成指定剧情节点。",
                    "重写或修订本章，使关键动作和证据与章纲对齐。",
                )
            )
        if not clue_hits:
            issues.append(
                make_issue(
                    "线索未推进",
                    "medium",
                    chapter_number,
                    "未命中核心线索词。",
                    "章节可能只有气氛或对话，没有推进疑案证据链。",
                    "加入账册、照影镜、安神丹、陆闻钟、禁地或审心室相关证据。",
                )
            )
        if repetitive_total >= 3:
            issues.append(
                make_issue(
                    "重复套话",
                    "low",
                    chapter_number,
                    f"重复短语统计：{repetitive_counts}",
                    "高频模板句会削弱人物动作和情绪表达。",
                    "用具体动作、视线、法器反应、环境压力替代沉默套话。",
                )
            )
        issues.extend(rule_boundary_issues(chapter_number, content))

    opening_counts = Counter(item for item in openings if item)
    for repeated, count in opening_counts.items():
        if count > 1:
            for chapter in chapters:
                if first_sentence(str(chapter.get("content") or "")) == repeated:
                    issues.append(
                        make_issue(
                            "节奏拖沓",
                            "low",
                            int(chapter["chapter_number"]),
                            f"重复开头：{repeated[:120]}",
                            "多章开头模式重复会削弱连载节奏。",
                            "改写开头承接方式，优先承接上一章结尾状态。",
                        )
                    )
    return sorted_issues(dedupe_issues(issues))


def rule_boundary_issues(chapter_number: int, content: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    checks = [
        (
            chapter_number <= 6 and re.search(r"陆闻钟.{0,12}(还活着|未死|尚在人世|神魂镇住)", content),
            "信息边界越界",
            "high",
            "林照夜前6章不能知道陆闻钟还活着。",
        ),
        (
            chapter_number <= 7 and re.search(r"陆闻钟.{0,12}(现身|走出|出现在|开口)", content),
            "伏笔提前泄露",
            "high",
            "陆闻钟前7章不能现身，只能通过线索逐步出现。",
        ),
        (
            chapter_number <= 4 and re.search(r"安神丹.{0,18}(换方|被换|改方)", content),
            "信息边界越界",
            "medium",
            "沈青蘅前4章不能知道安神丹被换方，只能发现药性异常。",
        ),
        (
            chapter_number <= 5 and re.search(r"谢无咎.{0,18}(完全信任|毫无保留|彻底相信)林照夜", content),
            "人物关系突变",
            "medium",
            "谢无咎前5章不能完全信任林照夜。",
        ),
        (
            re.search(r"林照夜.{0,30}(击败|斩杀|重创|压制).{0,20}(筑基|谢无咎|金丹|玄微)", content),
            "境界规则冲突",
            "high",
            "林照夜是炼气八层，不能正面击败筑基或金丹。",
        ),
        (
            re.search(r"谢无咎.{0,30}(击败|斩杀|重创|压制).{0,20}(金丹|玄微真人)", content),
            "境界规则冲突",
            "high",
            "谢无咎是筑基初期，不能强行抗衡金丹。",
        ),
        (
            chapter_number < 9 and re.search(r"(玄微真人|掌门).{0,30}(灵脉污染|隐瞒.{0,8}灵脉)", content),
            "伏笔提前泄露",
            "high",
            "掌门隐瞒灵脉污染的真相应在第9章附近指向，不能提前坐实。",
        ),
    ]
    for matched, issue_type, severity, evidence in checks:
        if matched:
            issues.append(
                make_issue(
                    issue_type,
                    severity,
                    chapter_number,
                    evidence,
                    "违反硬性规则会破坏悬疑推进和读者信任。",
                    "调整为怀疑、旁证或误导线索，不要让角色提前获得结论。",
                )
            )
    return issues


def first_sentence(text: str) -> str:
    parts = re.split(r"[。！？\n]+", text.strip(), maxsplit=1)
    return re.sub(r"\s+", "", parts[0].strip())[:120] if parts else ""


def make_issue(issue_type: str, severity: str, chapter_number: int, evidence: str, impact: str, suggestion: str) -> dict[str, Any]:
    normalized_type = issue_type if issue_type in ISSUE_TYPES else "线索未推进"
    normalized_severity = severity if severity in SEVERITY_ORDER else "medium"
    return {
        "type": normalized_type,
        "severity": normalized_severity,
        "chapter_number": chapter_number,
        "evidence": str(evidence or "")[:320],
        "impact": impact,
        "suggestion": suggestion,
        "source": "deterministic",
        "quality_category": issue_quality_category({"type": normalized_type, "severity": normalized_severity}),
    }


def dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for issue in issues:
        key = (issue.get("type"), issue.get("severity"), issue.get("chapter_number"), issue.get("evidence"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def sorted_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        issues,
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity")), 99),
            int(item.get("chapter_number") or 0),
            str(item.get("type") or ""),
        ),
    )


def issue_quality_category(issue: dict[str, Any]) -> str:
    constraint_type = str(issue.get("constraint_type") or "")
    issue_type = str(issue.get("type") or issue.get("issue_type") or "")
    if constraint_type in STYLE_CONSTRAINT_TYPES or issue_type in {"evolution_style_drift", "文风漂移"}:
        return "style"
    if issue_type in CONTINUITY_ISSUE_TYPES or constraint_type in {
        "location_transition",
        "entity_identity",
        "character_state",
        "time_pressure",
        "object_state",
        "goal_state",
        "threat_state",
    }:
        return "continuity"
    return "story_quality"


def build_quality_summary(issues: list[dict[str, Any]]) -> dict[str, Any]:
    continuity = [item for item in issues if issue_quality_category(item) == "continuity"]
    style = [item for item in issues if issue_quality_category(item) == "style"]
    return {
        "continuity_blocking_count": sum(
            1 for item in continuity if str(item.get("severity") or "") in {"critical", "high", "blocking", "needs_review"}
        ),
        "continuity_issue_count": len(continuity),
        "style_warning_count": sum(1 for item in style if str(item.get("severity") or "") == "warning"),
        "style_needs_review_count": sum(1 for item in style if str(item.get("severity") or "") in {"needs_review", "critical", "high"}),
        "style_issue_count": len(style),
        "story_quality_issue_count": sum(1 for item in issues if issue_quality_category(item) == "story_quality"),
    }


def build_llm_review_prompt(chapters: list[dict[str, Any]], deterministic: list[dict[str, Any]]) -> str:
    chapter_text = "\n\n".join(
        f"## 第{item['chapter_number']}章\n{str(item.get('content') or '')[:4200]}" for item in chapters
    )
    return f"""你是小说工程化审稿员。请检查这部10章仙侠宗门悬疑小说有哪些问题。

只输出 JSON 对象，不要 Markdown。JSON schema:
{{
  "issues": [
    {{
      "type": "章节大纲偏离|章节承接失败|境界规则冲突|信息边界越界|人物关系突变|宗门规则漂移|伏笔提前泄露|线索未推进|重复套话|节奏拖沓|字数异常|题材/世界观漂移",
      "severity": "critical|high|medium|low",
      "chapter_number": 1,
      "evidence": "短证据片段，80字内",
      "impact": "问题影响",
      "suggestion": "修复方向"
    }}
  ],
  "overall_assessment": "总体判断，120字内"
}}

固定设定:
书名：《{EXPERIMENT_SPEC['title']}》
类型：{EXPERIMENT_SPEC['genre']}
世界观：{EXPERIMENT_SPEC['world_preset']}
前提：{EXPERIMENT_SPEC['premise']}

主要人物:
{chr(10).join('- ' + item for item in EXPERIMENT_SPEC['characters'])}

硬性规则:
{chr(10).join('- ' + item for item in EXPERIMENT_SPEC['fixed_rules'])}

10章章纲:
{chr(10).join(f'{idx}. {outline}' for idx, outline in enumerate(EXPERIMENT_SPEC['chapter_outlines'], start=1))}

确定性检查已发现的问题:
{json.dumps(deterministic[:80], ensure_ascii=False, indent=2)}

正文:
{chapter_text}
"""


def run_llm_review(chapters: list[dict[str, Any]], deterministic: list[dict[str, Any]], *, timeout: int = 240) -> dict[str, Any]:
    if shutil.which("claude") is None:
        return {"status": "skipped", "reason": "claude_cli_not_found", "issues": [], "overall_assessment": ""}
    prompt = build_llm_review_prompt(chapters, deterministic)
    try:
        proc = subprocess.run(
            ["claude", "-p", "--permission-mode", "default", "--output-format", "json"],
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "issues": [], "overall_assessment": ""}
    if proc.returncode != 0:
        return {"status": "failed", "reason": proc.stderr.strip()[:500], "issues": [], "overall_assessment": ""}
    raw = proc.stdout.strip()
    try:
        payload = json.loads(raw)
        content = str(payload.get("result") or "")
    except json.JSONDecodeError:
        content = raw
    parsed = parse_llm_issue_json(content)
    if parsed is None:
        return {"status": "failed", "reason": "llm_json_parse_failed", "raw_preview": content[:800], "issues": [], "overall_assessment": ""}
    return {"status": "ok", **parsed}


def parse_llm_issue_json(content: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?", "", content.strip())
    text = re.sub(r"```$", "", text.strip())
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    issues = []
    for item in payload.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issue = make_issue(
            str(item.get("type") or "线索未推进"),
            str(item.get("severity") or "medium"),
            int(item.get("chapter_number") or 0),
            str(item.get("evidence") or ""),
            str(item.get("impact") or ""),
            str(item.get("suggestion") or ""),
        )
        issue["source"] = "llm"
        issues.append(issue)
    return {"issues": issues, "overall_assessment": str(payload.get("overall_assessment") or "")[:500]}


def build_report(run_dir: Path, novel_id: str, *, no_llm: bool = False) -> dict[str, Any]:
    chapters = load_chapters_from_run(run_dir, novel_id)
    deterministic = deterministic_issues(chapters, expected_chapters=10)
    llm = {"status": "not_run", "issues": [], "overall_assessment": ""} if no_llm else run_llm_review(chapters, deterministic)
    all_issues = sorted_issues(dedupe_issues(deterministic + list(llm.get("issues") or [])))
    counts_by_severity = Counter(str(item.get("severity") or "medium") for item in all_issues)
    counts_by_type = Counter(str(item.get("type") or "") for item in all_issues)
    quality_summary = build_quality_summary(all_issues)
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "run_dir": str(run_dir),
        "novel_id": novel_id,
        "theme": {
            "title": EXPERIMENT_SPEC["title"],
            "genre": EXPERIMENT_SPEC["genre"],
            "target_chapters": EXPERIMENT_SPEC["target_chapters"],
            "target_chars_per_chapter": 2500,
        },
        "chapter_count": len(chapters),
        "llm_review": {key: value for key, value in llm.items() if key != "issues"},
        "summary": {
            "issue_count": len(all_issues),
            "blocking_issue_count": sum(counts_by_severity.get(item, 0) for item in ("critical", "high")),
            **quality_summary,
            "counts_by_severity": dict(counts_by_severity),
            "counts_by_type": dict(counts_by_type),
        },
        "issues": all_issues,
    }


def write_report(run_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    json_path = run_dir / "article_issue_report.json"
    md_path = run_dir / "article_issue_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Article Issue Report",
        "",
        f"- Novel: `{report.get('novel_id')}`",
        f"- Theme: 《{(report.get('theme') or {}).get('title')}》 / {(report.get('theme') or {}).get('genre')}",
        f"- Chapters: `{report.get('chapter_count')}`",
        f"- Issue count: `{(report.get('summary') or {}).get('issue_count')}`",
        f"- Continuity blocking: `{(report.get('summary') or {}).get('continuity_blocking_count')}`",
        f"- Style warnings / review: `{(report.get('summary') or {}).get('style_warning_count')}` / `{(report.get('summary') or {}).get('style_needs_review_count')}`",
        f"- LLM review: `{(report.get('llm_review') or {}).get('status')}`",
        "",
        "## Issues",
        "",
    ]
    issues = report.get("issues") or []
    if not issues:
        lines.append("- 未发现阻断问题；`issues` 为空。")
    for issue in issues:
        lines.extend(
            [
                f"### [{issue.get('severity')}] 第{issue.get('chapter_number')}章 · {issue.get('type')}",
                "",
                f"- Evidence: {issue.get('evidence')}",
                f"- Impact: {issue.get('impact')}",
                f"- Suggestion: {issue.get('suggestion')}",
                f"- Source: `{issue.get('source')}`",
                "",
            ]
        )
    assessment = (report.get("llm_review") or {}).get("overall_assessment")
    if assessment:
        lines.extend(["## LLM Overall Assessment", "", str(assessment), ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write article_issue_report.json/md for a frontend Evolution pressure run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--no-llm", action="store_true", help="Only run deterministic checks.")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    report = build_report(run_dir, args.novel_id, no_llm=args.no_llm)
    json_path, md_path = write_report(run_dir, report)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
