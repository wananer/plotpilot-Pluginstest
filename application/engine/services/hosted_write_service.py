"""托管连写：自动规划大纲 + 按章流式生成 + 可选落库，上下文由 ContextBuilder 维护。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional, TYPE_CHECKING

from application.core.services.chapter_service import ChapterService
from application.core.services.novel_service import NovelService
from application.ai.llm_audit import llm_audit_context
from application.ai.llm_output_sanitize import strip_reasoning_artifacts
from application.workflows.auto_novel_generation_workflow import AutoNovelGenerationWorkflow
from domain.ai.services.llm_service import GenerationConfig, LLMService
from domain.ai.value_objects.prompt import Prompt
from domain.shared.exceptions import EntityNotFoundError
from plugins.platform.host_integration import review_chapter_with_plugins
if TYPE_CHECKING:
    from application.engine.services.chapter_aftermath_pipeline import ChapterAftermathPipeline

logger = logging.getLogger(__name__)

BOUNDARY_REVISION_MAX_ATTEMPTS = 2
BOUNDARY_REVIEW_SOURCE = "hosted_write_boundary_gate"
BOUNDARY_REVIEW_RECHECK_SOURCE = "hosted_write_boundary_gate_recheck"
BOUNDARY_ISSUE_TYPES = {
    "evolution_boundary_location_jump",
    "evolution_route_missing_transition",
    "evolution_character_state_drop",
    "evolution_unresolved_cliffhanger_skip",
    "evolution_boundary_goal_skip",
    "evolution_missing_time_bridge",
    "evolution_entity_identity_drift",
    "evolution_time_pressure_drift",
    "evolution_constraint_location_transition",
    "evolution_constraint_character_state",
    "evolution_constraint_unfulfilled",
}


class HostedWriteService:
    """多章连续托管写作（单连接 SSE 推送全程事件）。"""

    def __init__(
        self,
        workflow: AutoNovelGenerationWorkflow,
        chapter_service: ChapterService,
        novel_service: NovelService,
        chapter_aftermath_pipeline: Optional["ChapterAftermathPipeline"] = None,
        llm_service: Optional[LLMService] = None,
    ):
        self._workflow = workflow
        self._chapter = chapter_service
        self._novel = novel_service
        self._aftermath = chapter_aftermath_pipeline
        self._llm = llm_service

    def _schedule_chapter_aftermath(self, novel_id: str, chapter_number: int, content: str) -> None:
        """与 HTTP 保存同源：叙事/向量、文风、KG（不阻塞 SSE）；三元组与伏笔在叙事同步单次 LLM 中落库。"""
        if not self._aftermath or not content.strip():
            return

        async def _run() -> None:
            try:
                dto = self._chapter.get_chapter_by_novel_and_number(novel_id, chapter_number)
                if not dto:
                    return
                await self._aftermath.run_after_chapter_saved(novel_id, chapter_number, content)
            except Exception as e:
                logger.warning(
                    "托管章后管线失败 novel=%s ch=%s: %s", novel_id, chapter_number, e
                )

        try:
            asyncio.create_task(_run())
        except Exception as e:
            logger.warning("托管章后管线未调度: %s", e)

    def _fallback_outline(self, novel_id: str, chapter_number: int) -> str:
        dto = self._chapter.get_chapter_by_novel_and_number(novel_id, chapter_number)
        title = dto.title if dto else f"第{chapter_number}章"
        return (
            f"【托管】{title}\n\n"
            "承接已有正文与设定，推进本章情节与人物；保持人称、时态与全书一致。"
        )

    async def _review_boundary_issues(
        self,
        novel_id: str,
        chapter_number: int,
        content: str,
        *,
        source: str,
    ) -> list[dict[str, Any]]:
        results = await review_chapter_with_plugins(
            novel_id,
            chapter_number,
            content,
            source=source,
        )
        issues: list[dict[str, Any]] = []
        for result in results:
            if result.get("plugin_name") != "world_evolution_core":
                continue
            if not result.get("ok", True) or result.get("skipped"):
                continue
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            for item in data.get("issues") or []:
                if not isinstance(item, dict):
                    continue
                issue_type = str(item.get("issue_type") or "")
                if issue_type in BOUNDARY_ISSUE_TYPES and item.get("revision_required"):
                    issues.append(item)
        return issues

    async def _maybe_revise_boundary_opening(
        self,
        novel_id: str,
        chapter_number: int,
        content: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        if not content.strip():
            return content, events

        try:
            issues = await self._review_boundary_issues(
                novel_id,
                chapter_number,
                content,
                source=BOUNDARY_REVIEW_SOURCE,
            )
        except Exception as exc:
            logger.warning("章节边界审查失败 novel=%s ch=%s: %s", novel_id, chapter_number, exc)
            events.append(
                {
                    "type": "boundary_revision_skipped",
                    "chapter": chapter_number,
                    "reason": "plugin_review_failed",
                    "message": str(exc),
                }
            )
            return content, events

        if not issues:
            events.append(
                {
                    "type": "boundary_revision_skipped",
                    "chapter": chapter_number,
                    "reason": "no_boundary_revision_required",
                }
            )
            return content, events

        if not self._llm:
            primary_issue = issues[0]
            brief = primary_issue.get("opening_revision_brief") if isinstance(primary_issue.get("opening_revision_brief"), dict) else {}
            events.append(
                {
                    "type": "boundary_revision_required",
                    "chapter": chapter_number,
                    "ok": False,
                    "reason": "llm_service_unavailable",
                    "revision_mode": primary_issue.get("revision_mode") or "manual_or_host_revision_required",
                    "opening_revision_brief": brief,
                }
            )
            return content, events

        revised_content = content
        current_issues = issues
        primary_issue = current_issues[0]
        brief = primary_issue.get("opening_revision_brief") if isinstance(primary_issue.get("opening_revision_brief"), dict) else {}

        for attempt in range(1, BOUNDARY_REVISION_MAX_ATTEMPTS + 1):
            primary_issue = current_issues[0]
            brief = primary_issue.get("opening_revision_brief") if isinstance(primary_issue.get("opening_revision_brief"), dict) else {}
            events.append(
                {
                    "type": "boundary_revision_start",
                    "chapter": chapter_number,
                    "attempt": attempt,
                    "issue_type": primary_issue.get("issue_type"),
                    "opening_revision_brief": brief,
                }
            )

            try:
                rewritten_opening = await self._rewrite_opening_with_llm(
                    novel_id,
                    chapter_number,
                    revised_content,
                    primary_issue,
                    attempt=attempt,
                )
                revised_content = _replace_opening(revised_content, rewritten_opening)
            except Exception as exc:
                logger.warning("章节边界开头重写失败 novel=%s ch=%s: %s", novel_id, chapter_number, exc)
                events.append(
                    {
                        "type": "boundary_revision_required",
                        "chapter": chapter_number,
                        "ok": False,
                        "reason": "rewrite_failed",
                        "message": str(exc),
                        "revision_mode": primary_issue.get("revision_mode") or "manual_or_host_revision_required",
                        "opening_revision_brief": brief,
                        "attempts": attempt,
                    }
                )
                return content if attempt == 1 else revised_content, events

            try:
                remaining = await self._review_boundary_issues(
                    novel_id,
                    chapter_number,
                    revised_content,
                    source=BOUNDARY_REVIEW_RECHECK_SOURCE,
                )
            except Exception as exc:
                logger.warning("章节边界复查失败 novel=%s ch=%s: %s", novel_id, chapter_number, exc)
                events.append(
                    {
                        "type": "boundary_revision_required",
                        "chapter": chapter_number,
                        "ok": False,
                        "reason": "recheck_failed",
                        "message": str(exc),
                        "revision_mode": primary_issue.get("revision_mode") or "manual_or_host_revision_required",
                        "opening_revision_brief": brief,
                        "attempts": attempt,
                    }
                )
                return revised_content, events

            if not remaining:
                events.append(
                    {
                        "type": "boundary_revision_applied",
                        "chapter": chapter_number,
                        "ok": True,
                        "attempts": attempt,
                        "issue_type": primary_issue.get("issue_type"),
                        "constraint_type": primary_issue.get("constraint_type"),
                        "auto_revised_reason": primary_issue.get("description") or primary_issue.get("issue_type"),
                        "before_opening": content[:500],
                        "after_opening_digest": rewritten_opening[:500],
                        "remaining_risk": False,
                        "content": revised_content,
                        "replaced_opening_chars": _opening_replacement_end(content),
                        "rewritten_opening_chars": len(rewritten_opening),
                    }
                )
                return revised_content, events

            current_issues = remaining

        events.append(
            {
                "type": "boundary_revision_required",
                "chapter": chapter_number,
                "ok": False,
                "reason": "recheck_still_failed",
                "revision_mode": primary_issue.get("revision_mode") or "manual_or_host_revision_required",
                "opening_revision_brief": brief,
                "remaining_issues": _summarize_boundary_issues(current_issues),
                "remaining_risk": True,
                "attempts": BOUNDARY_REVISION_MAX_ATTEMPTS,
            }
        )
        return revised_content, events

    async def _rewrite_opening_with_llm(
        self,
        novel_id: str,
        chapter_number: int,
        content: str,
        issue: dict[str, Any],
        *,
        attempt: int,
    ) -> str:
        brief = issue.get("opening_revision_brief") if isinstance(issue.get("opening_revision_brief"), dict) else {}
        route_state = brief.get("route_state") if isinstance(brief.get("route_state"), dict) else {}
        character_positions = brief.get("character_positions") if isinstance(brief.get("character_positions"), dict) else {}
        continuity_constraints = brief.get("continuity_constraints") if isinstance(brief.get("continuity_constraints"), list) else []
        prompt = Prompt(
            system=(
                "你是小说章节开头修订器。只输出替换后的开头段落，不要解释、不要JSON、不要Markdown。"
                "你的任务是补足相邻章节承接，不改变后续剧情事实，不重写整章。"
            ),
            user=(
                f"小说ID：{novel_id}\n"
                f"章节：第{chapter_number}章\n"
                f"上一章结尾证据：{str(brief.get('previous_ending_evidence') or '')[:500]}\n"
                f"上一章路线状态：{str(route_state)[:500]}\n"
                f"上一章人物位置状态：{str(character_positions)[:500]}\n"
                f"统一连续性约束：{str(continuity_constraints)[:800]}\n"
                f"当前开头问题：{str(brief.get('current_opening_problem') or issue.get('description') or '')[:500]}\n"
                f"必须补的桥接类型：{brief.get('required_bridge_type') or '移动/撤离/跳时/失败/视角桥接'}\n"
                "修订要求：\n"
                "1. 只写本章新的开头100-300字。\n"
                "2. 开头必须先处理上一章尾钩、地点、目标或即时威胁。\n"
                "3. 如果换地点，必须写清移动、撤离、失败、跳时或视角桥接。\n"
                "4. 如果上一章人物受伤、被追踪、携带关键物件、倒计时或分离，必须在开头交代状态变化。\n"
                "5. 不要输出系统提示、规划术语、JSON、schema、T0/T1、解释文字。\n"
                "6. 后续正文会原样保留，所以新开头必须自然衔接下面的原文。\n\n"
                f"原章节开头800字：\n{content[:800]}"
            ),
        )
        config = GenerationConfig(max_tokens=700, temperature=0.35)
        with llm_audit_context(
            novel_id=novel_id,
            chapter_number=chapter_number,
            phase="hosted_write_boundary_revision",
            rewrite_attempt=attempt,
            source="hosted_write_service._rewrite_opening_with_llm",
        ):
            result = await self._llm.generate(prompt, config)
        return _sanitize_rewritten_opening(result.content)

    async def stream_hosted_write(
        self,
        novel_id: str,
        from_chapter: int,
        to_chapter: int,
        auto_save: bool = True,
        auto_outline: bool = True,
    ) -> AsyncIterator[Dict[str, Any]]:
        """按章节区间连续生成；每章先大纲（LLM 或模板），再复用 generate_chapter_stream。

        事件在单章事件上增加 ``chapter``；并可能发出 ``session`` / ``chapter_start`` /
        ``outline`` / ``saved``。
        """
        logger.info(f"========================================")
        logger.info(f"开始托管连写: 小说={novel_id}, 章节范围={from_chapter}-{to_chapter}")
        logger.info(f"配置: auto_save={auto_save}, auto_outline={auto_outline}")
        logger.info(f"========================================")

        if from_chapter < 1 or to_chapter < 1 or to_chapter < from_chapter:
            logger.error(f"无效的章节范围: {from_chapter}-{to_chapter}")
            yield {"type": "error", "message": "invalid chapter range"}
            return

        total = to_chapter - from_chapter + 1
        logger.info(f"总计需要生成 {total} 个章节")

        yield {
            "type": "session",
            "novel_id": novel_id,
            "from_chapter": from_chapter,
            "to_chapter": to_chapter,
            "total": total,
        }

        for index, n in enumerate(range(from_chapter, to_chapter + 1), start=1):
            logger.info(f"----------------------------------------")
            logger.info(f"开始处理章节 {n} ({index}/{total})")
            logger.info(f"----------------------------------------")

            yield {"type": "chapter_start", "chapter": n, "index": index, "total": total}

            if auto_outline:
                try:
                    logger.info(f"  → 使用 LLM 生成章节 {n} 的大纲")
                    outline = await self._workflow.suggest_outline(novel_id, n)
                    logger.info(f"  ✓ 大纲生成成功: {len(outline)} 字符")
                except Exception as e:
                    logger.warning(f"  × 大纲生成失败: {e}, 使用默认模板")
                    outline = self._fallback_outline(novel_id, n)
            else:
                logger.info(f"  → 使用默认大纲模板")
                outline = self._fallback_outline(novel_id, n)

            yield {"type": "outline", "chapter": n, "text": outline}

            async for ev in self._workflow.generate_chapter_stream(novel_id, n, outline, enable_beats=True):
                merged: Dict[str, Any] = dict(ev)
                merged["chapter"] = n
                yield merged

                if ev.get("type") == "done" and auto_save:
                    content = ev.get("content") or ""
                    content, boundary_revision_events = await self._maybe_revise_boundary_opening(
                        novel_id,
                        n,
                        content,
                    )
                    for revision_event in boundary_revision_events:
                        yield revision_event
                    logger.info(f"  → 尝试保存章节 {n} ({len(content)} 字符)")
                    try:
                        # 先尝试更新已存在的章节
                        self._chapter.update_chapter_by_novel_and_number(
                            novel_id, n, content
                        )
                        logger.info(f"  ✓ 章节 {n} 更新成功")
                        self._schedule_chapter_aftermath(novel_id, n, content)
                        yield {"type": "saved", "chapter": n, "ok": True}
                    except EntityNotFoundError as e:
                        # 章节不存在，创建新章节
                        logger.info(f"  → 章节 {n} 不存在，创建新章节")
                        try:
                            chapter_id = f"chapter-{novel_id}-{n}"
                            title = f"第{n}章"
                            self._novel.add_chapter(
                                novel_id=novel_id,
                                chapter_id=chapter_id,
                                number=n,
                                title=title,
                                content=content
                            )
                            logger.info(f"  ✓ 章节 {n} 创建成功")
                            self._schedule_chapter_aftermath(novel_id, n, content)
                            yield {"type": "saved", "chapter": n, "ok": True, "created": True}
                        except (ValueError, Exception) as create_ex:
                            logger.error(f"  × 创建章节 {n} 失败: {type(create_ex).__name__}: {create_ex}")
                            yield {
                                "type": "saved",
                                "chapter": n,
                                "ok": False,
                                "message": f"创建章节失败: {create_ex}",
                            }
                    except Exception as ex:
                        logger.error(f"  × 保存章节 {n} 时发生异常: {type(ex).__name__}: {ex}")
                        yield {
                            "type": "saved",
                            "chapter": n,
                            "ok": False,
                            "message": str(ex),
                        }

                if ev.get("type") == "error":
                    logger.error(f"  × 章节 {n} 生成失败，终止托管连写")
                    return

        logger.info(f"========================================")
        logger.info(f"托管连写完成: 小说={novel_id}, 共生成 {total} 个章节")
        logger.info(f"========================================")
        yield {"type": "session_done", "novel_id": novel_id}


def _sanitize_rewritten_opening(value: str) -> str:
    text = strip_reasoning_artifacts(str(value or "")).strip()
    text = text.removeprefix("```text").removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
    if not text:
        raise ValueError("boundary revision opening is empty")
    lowered = text.lower()
    blocked_terms = ["{", "}", "```", "json", "schema", "t0", "t1", "系统内部", "勿向读者展示", "opening_revision_brief"]
    if any(term in lowered for term in blocked_terms):
        raise ValueError("boundary revision opening contains non-manuscript artifacts")
    if len(text) > 900:
        raise ValueError("boundary revision opening is too long")
    return text


def _replace_opening(content: str, rewritten_opening: str) -> str:
    body = str(content or "")
    opening = str(rewritten_opening or "").strip()
    if not body.strip() or not opening:
        return body
    cut = _opening_replacement_end(body)
    remainder = body[cut:].lstrip()
    if not remainder:
        return opening
    separator = "\n\n" if "\n\n" not in opening[-4:] else ""
    return f"{opening}{separator}{remainder}"


def _opening_replacement_end(content: str) -> int:
    body = str(content or "")
    if not body:
        return 0
    search_limit = min(len(body), 420)
    double_break = body.find("\n\n", 1, search_limit)
    if double_break != -1:
        return double_break + 2
    single_break = body.find("\n", 40, search_limit)
    if single_break != -1:
        return single_break + 1
    sentence_marks = [body.find(mark, 40, search_limit) for mark in ("。", "！", "？", "!", "?")]
    if len(body) < 120:
        sentence_marks.extend(body.find(mark, 1, search_limit) for mark in ("。", "！", "？", "!", "?"))
    sentence_marks = [index for index in sentence_marks if index != -1]
    if sentence_marks:
        return min(sentence_marks) + 1
    return min(len(body), 300)


def _summarize_boundary_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "issue_type": issue.get("issue_type"),
            "description": str(issue.get("description") or "")[:240],
            "revision_required": bool(issue.get("revision_required")),
        }
        for issue in issues[:4]
        if isinstance(issue, dict)
    ]
