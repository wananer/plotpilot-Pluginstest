"""Deterministic supplementary review rules for Evolution World."""
from __future__ import annotations

import re
from typing import Any, Optional

from .continuity import review_continuity_constraints_against_content

PLUGIN_NAME = "world_evolution_core"

REPETITION_PHRASES = [
    "没有说话",
    "没有回答",
    "喉咙发紧",
    "深吸一口气",
    "沉默几秒",
    "沉默了几秒",
    "声音很轻",
    "掌心发烫",
    "像是等",
]

BOUNDARY_BRIDGE_MARKERS = [
    "离开",
    "撤离",
    "赶往",
    "沿着",
    "穿过",
    "绕过",
    "绕路",
    "数小时后",
    "第二天",
    "没能进入",
    "追到",
    "逃到",
    "逃离",
    "冲出",
    "半小时后",
    "几分钟后",
    "被打断",
    "改道",
    "退回",
    "撤到",
    "返回",
    "路上",
    "途中",
    "坠入",
    "摔进",
    "钻进",
    "爬出",
    "分头",
    "汇合",
]
BOUNDARY_LOCATION_MARKERS = [
    "B3",
    "B3-07",
    "B5",
    "C307",
    "AI核心机房",
    "核心机房",
    "礼堂后台",
    "礼堂",
    "塔顶",
    "水箱",
    "电梯井",
    "消防梯",
    "安全屋",
    "控制室",
    "锅炉房",
    "舞台",
    "后台",
    "地下大厅",
    "档案馆侧门",
    "档案馆",
    "城市记忆存储站03号节点",
    "废弃工厂",
    "回收站",
    "D区",
    "中枢大楼",
]
CHARACTER_STATE_MARKERS = ("受伤", "流血", "昏迷", "被追", "追踪", "倒计时", "携带", "拿着", "握着", "背包", "口袋", "坠入", "跌入", "被困")


def character_is_mentioned(card: dict[str, Any], content: str) -> bool:
    names = [card.get("name"), *(card.get("aliases") or [])]
    return any(str(name or "").strip() and str(name).strip() in content for name in names)


def review_character_card_against_content(card: dict[str, Any], content: str, chapter_number: int) -> list[dict[str, Any]]:
    name = str(card.get("name") or "角色").strip()
    issues: list[dict[str, Any]] = []
    cognitive = card.get("cognitive_state") if isinstance(card.get("cognitive_state"), dict) else {}
    for unknown in _as_strings(cognitive.get("unknowns")):
        if _looks_resolved_without_transition(content, unknown):
            issues.append(
                review_issue(
                    "evolution_character_cognition",
                    "warning",
                    f"{name} 在人物卡中仍标记为未知：{unknown}，但本章像是直接知道/利用了该信息。",
                    chapter_number,
                    "补充他如何得知、推断或误判这条信息；如果只是猜测，请在文本中保留不确定性。",
                )
            )
    for misbelief in _as_strings(cognitive.get("misbeliefs")):
        if _mentions_key_terms(content, misbelief) and not _has_transition_marker(content):
            issues.append(
                review_issue(
                    "evolution_character_belief",
                    "suggestion",
                    f"{name} 仍有未修正误信：{misbelief}，本章相关表述需要交代误信是否被打破。",
                    chapter_number,
                    "写出证据、挫败或他人的告知，让认知变化成为剧情事件，而不是静默切换。",
                )
            )
    for limit in _as_strings(card.get("capability_limits")):
        if _mentions_key_terms(content, limit) and _has_mastery_marker(content) and not _has_transition_marker(content):
            issues.append(
                review_issue(
                    "evolution_character_capability",
                    "warning",
                    f"{name} 的能力边界是：{limit}，但本章呈现为直接突破或熟练解决。",
                    chapter_number,
                    "增加试错、代价、外部帮助或失败风险；避免把能力边界写成突然全知全能。",
                )
            )
    if _has_all_knowing_marker(content) and (_as_strings(cognitive.get("unknowns")) or _as_strings(card.get("capability_limits"))):
        issues.append(
            review_issue(
                "evolution_character_logic",
                "suggestion",
                f"{name} 本章语气接近全知判断，但人物卡仍存在未知或能力边界。",
                chapter_number,
                "将确定判断改为观察、推断、误判或带代价的验证，让角色认知随证据成长。",
            )
        )
    palette = card.get("personality_palette") if isinstance(card.get("personality_palette"), dict) else {}
    missing_palette_fields = _missing_palette_fields(palette)
    if missing_palette_fields:
        issue = review_issue(
            "evolution_palette_missing",
            "warning",
            f"{name} 本章出场，但人物卡性格调色盘仍缺少：{', '.join(missing_palette_fields)}。",
            chapter_number,
            "不要只写性格标签；请从本章动作、选择和关系反应中推断底色、主色调与点缀。",
        )
        issue["evidence"] = [{"character": name, "missing_fields": missing_palette_fields}]
        issues.append(issue)
    elif _looks_like_palette_drift(content) and not _has_transition_marker(content):
        issue = review_issue(
            "evolution_palette_drift",
            "warning",
            f"{name} 本章出现明显性格反转/漂移表述，但缺少情境压力、关系触发或成长过渡。",
            chapter_number,
            "如要违背既有调色盘，请写出触发条件；否则让行为回到既有底色、主色调和点缀的衍生范围。",
        )
        issue["evidence"] = [
            {
                "character": name,
                "base": palette.get("base"),
                "main_tones": _as_strings(palette.get("main_tones"))[:4],
                "sample": str(content or "")[:240],
            }
        ]
        issues.append(issue)
    return issues


def review_issue(issue_type: str, severity: str, description: str, chapter_number: int, suggestion: str) -> dict[str, Any]:
    return {
        "issue_type": issue_type,
        "severity": severity,
        "description": description,
        "location": f"Chapter {chapter_number}",
        "suggestion": suggestion,
    }


def normalize_evolution_issue_metadata(issue: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(issue)
    issue_type = str(normalized.get("issue_type") or "")
    if issue_type.startswith("evolution_"):
        normalized.setdefault("source_plugin", PLUGIN_NAME)
    normalized.setdefault("issue_family", _issue_family(issue_type))
    normalized.setdefault("suggestion", "")
    evidence = normalized.get("evidence")
    if evidence is None:
        normalized["evidence"] = []
    elif isinstance(evidence, dict):
        normalized["evidence"] = [evidence]
    elif not isinstance(evidence, list):
        normalized["evidence"] = [{"value": str(evidence)}]
    if "host_source_refs" not in normalized:
        refs = []
        for item in normalized.get("evidence") or []:
            if isinstance(item, dict) and (item.get("source") or item.get("source_type") or item.get("id")):
                refs.append(
                    {
                        "source": item.get("source") or item.get("source_type") or "",
                        "id": item.get("id"),
                        "source_type": item.get("source_type"),
                    }
                )
        normalized["host_source_refs"] = refs
    return normalized


def review_host_context_against_content(host_context: dict[str, Any], content: str, chapter_number: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    text = str(content or "")
    for source, issue_type, label in (
        ("bible", "evolution_bible_context", "Bible 人物/地点边界"),
        ("world", "evolution_worldbuilding_context", "世界观/地点设定"),
        ("knowledge", "evolution_knowledge_context", "知识库事实"),
        ("story_knowledge", "evolution_story_knowledge_context", "章后叙事同步"),
        ("storyline", "evolution_storyline_context", "故事线"),
        ("timeline", "evolution_timeline_context", "时间线"),
        ("chronicle", "evolution_chronicle_context", "编年史"),
        ("foreshadow", "evolution_foreshadow_context", "伏笔账本"),
        ("dialogue", "evolution_dialogue_voice_context", "对话声线样本"),
        ("triples", "evolution_triples_context", "图谱三元组"),
        ("memory_engine", "evolution_memory_engine_context", "MemoryEngine fact lock"),
    ):
        matches = _host_context_mentions(host_context.get(source) or [], text)
        if not matches:
            continue
        issue = review_issue(
            issue_type,
            "warning",
            f"本章触及 PlotPilot {label} 中的既有信息：{', '.join(str(item.get('name') or item.get('id') or '') for item in matches[:3])}。",
            chapter_number,
            f"写作与审查时应显式核对 {label}；如要偏离，需要在正文中给出转场、解释、误导或回收依据。",
        )
        evidence = [
            {
                "source": source,
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "source_type": item.get("source_type"),
            }
            for item in matches[:4]
        ]
        issue["source_plugin"] = PLUGIN_NAME
        issue["issue_family"] = source
        issue["host_source_refs"] = [{"source": item["source"], "id": item.get("id"), "source_type": item.get("source_type")} for item in evidence]
        issue["evidence"] = evidence
        issues.append(issue)
    return issues[:6]


def review_style_repetition(content: str, chapter_number: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for phrase in REPETITION_PHRASES:
        count = str(content or "").count(phrase)
        if count < 4:
            continue
        issue = review_issue(
            "evolution_style_repetition",
            "warning",
            f"本章高频重复反应模板「{phrase}」出现 {count} 次，容易形成机械化表达。",
            chapter_number,
            replacement_guidance_for_phrase(phrase),
        )
        issue["evidence"] = [{"phrase": phrase, "count": count, "sample": _sample_phrase_context(content, phrase)}]
        issues.append(issue)
    return issues


def review_extraction_pollution(cards: list[dict[str, Any]], facts: list[dict[str, Any]], chapter_number: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    invalid_cards = [card for card in cards if str(card.get("status") or "") == "invalid_entity"]
    if invalid_cards:
        names = [str(card.get("name") or "") for card in invalid_cards[:6]]
        issue = review_issue(
            "evolution_character_pollution",
            "warning",
            f"人物卡检测到非人物实体污染：{', '.join(names)}。",
            chapter_number,
            "将物品、方向、查询记录、抽象概念放入 world facts 或 props，不要注入人物卡主上下文。",
        )
        issue["evidence"] = [
            {
                "names": names,
                "count": len(invalid_cards),
                "entities": [
                    {
                        "name": str(card.get("name") or ""),
                        "first_seen_chapter": card.get("first_seen_chapter"),
                        "last_seen_chapter": card.get("last_seen_chapter"),
                        "invalid_reason": card.get("invalid_reason"),
                    }
                    for card in invalid_cards[:6]
                ],
            }
        ]
        issues.append(issue)
    bad_locations: list[str] = []
    for fact in facts:
        for location in fact.get("locations") or []:
            value = str(location or "").strip()
            if value in {"但他咬牙站", "个信息站", "老板专门", "道防火门"} or any(token in value for token in ("咬牙", "老板", "专门")):
                bad_locations.append(value)
    if bad_locations:
        issue = review_issue(
            "evolution_location_pollution",
            "warning",
            f"地点列表检测到疑似半句残片：{', '.join(bad_locations[:6])}。",
            chapter_number,
            "地点必须是空间名词、地图节点或上下文位置表达；动词残片和半句不要进入路线图。",
        )
        issue["evidence"] = [{"locations": bad_locations[:8], "count": len(bad_locations)}]
        issues.append(issue)
    return issues


class BoundaryContinuityGate:
    """Validate that a new chapter opening pays off the previous ending."""

    def __init__(self, opening_chars: int = 800) -> None:
        self.opening_chars = max(500, min(int(opening_chars or 800), 800))

    def check(self, previous_summaries: list[dict[str, Any]], content: str, chapter_number: int) -> dict[str, Any]:
        issues = self.review(previous_summaries, content, chapter_number)
        return {
            "gate": "boundary_continuity",
            "passed": not any(issue.get("revision_required") for issue in issues),
            "revision_required": any(issue.get("revision_required") for issue in issues),
            "opening_window_chars": self.opening_chars,
            "issues": issues,
        }

    def review(self, previous_summaries: list[dict[str, Any]], content: str, chapter_number: int) -> list[dict[str, Any]]:
        if not previous_summaries:
            return []
        previous = previous_summaries[-1]
        carry = previous.get("carry_forward") if isinstance(previous.get("carry_forward"), dict) else {}
        boundary = carry.get("boundary_state") if isinstance(carry.get("boundary_state"), dict) else {}
        previous_locations = [str(item) for item in carry.get("last_known_locations") or [] if str(item).strip()]
        route_state = carry.get("route_state") if isinstance(carry.get("route_state"), dict) else {}
        if not route_state and isinstance(carry.get("continuity_route_state"), dict):
            route_state = carry.get("continuity_route_state")
        character_positions = carry.get("character_positions") if isinstance(carry.get("character_positions"), dict) else {}
        ending_location = str(boundary.get("ending_location") or "").strip()
        if ending_location:
            previous_locations = _dedupe([ending_location, *previous_locations])
        route_end = str(route_state.get("end_location") or "").strip()
        if route_end:
            previous_locations = _dedupe([route_end, *previous_locations])
        if not previous_locations and not boundary:
            return []

        opening = str(content or "")[: self.opening_chars]
        issues: list[dict[str, Any]] = []
        if any(location and location in opening for location in previous_locations):
            if any(token in opening for token in ("才找到", "第一次找到", "重新进入", "又一次进入", "再次抵达", "终于找到")):
                issues.append(
                    _boundary_issue(
                        "evolution_boundary_location_jump",
                        chapter_number,
                        "上一章结尾已将角色停在同一地点，本章开头又写成重新/首次抵达，疑似章节首尾回滚。",
                        previous,
                        opening,
                    )
                )

        opening_locations = _extract_boundary_locations(opening)
        changed_location = bool(previous_locations and opening_locations and not set(previous_locations) & set(opening_locations))
        if changed_location and not _has_boundary_bridge(opening, previous_locations=previous_locations, current_locations=opening_locations):
            issues.append(
                _boundary_issue(
                    "evolution_route_missing_transition",
                    chapter_number,
                    f"上一章终点在 {', '.join(previous_locations[:3])}，本章开头切换地点但缺少明确移动/跳时桥段。",
                    previous,
                    opening,
                )
            )
        elif not opening_locations and previous_locations and any(token in opening for token in ("回到", "来到", "抵达", "进入", "走进")) and not _has_boundary_bridge(opening, previous_locations=previous_locations):
            issues.append(
                _boundary_issue(
                    "evolution_route_missing_transition",
                    chapter_number,
                    f"上一章终点在 {', '.join(previous_locations[:3])}，本章开头有抵达/进入动作但未交代从上一终点如何过渡。",
                    previous,
                    opening,
                )
            )

        if route_state and _route_state_requires_bridge(route_state, opening, previous_locations, opening_locations):
            issues.append(
                _boundary_issue(
                    "evolution_route_missing_transition",
                    chapter_number,
                    "上一章路线状态要求交代移动路径、耗时、同行/分离人物或威胁变化，但本章开头没有可见路线桥。",
                    previous,
                    opening,
                )
            )

        character_issue = _character_state_issue(character_positions, opening)
        if character_issue:
            issues.append(
                _boundary_issue(
                    "evolution_character_state_drop",
                    chapter_number,
                    character_issue,
                    previous,
                    opening,
                )
            )

        cliffhanger = str(boundary.get("unresolved_cliffhanger") or "").strip()
        threat = str(boundary.get("immediate_threat") or "").strip()
        if (cliffhanger or threat) and not _boundary_terms_handled(opening, cliffhanger or threat):
            issues.append(
                _boundary_issue(
                    "evolution_unresolved_cliffhanger_skip",
                    chapter_number,
                    "上一章留下即时威胁/尾钩，但本章开头没有兑现、撤离、被打断或解释后果。",
                    previous,
                    opening,
                )
            )

        goal = str(boundary.get("active_goal") or "").strip()
        if goal and not _boundary_terms_handled(opening, goal) and not _has_boundary_bridge(opening, previous_locations=previous_locations, current_locations=opening_locations):
            issues.append(
                _boundary_issue(
                    "evolution_boundary_goal_skip",
                    chapter_number,
                    "上一章结尾目标未在本章开头被继续、失败、中断或改道，读者会感到行动链断裂。",
                    previous,
                    opening,
                )
            )
        issues.extend(review_continuity_constraints_against_content(previous, opening, chapter_number, opening_chars=self.opening_chars))
        return issues[:6]


def review_boundary_state(previous_summaries: list[dict[str, Any]], content: str, chapter_number: int) -> list[dict[str, Any]]:
    return BoundaryContinuityGate().review(previous_summaries, content, chapter_number)


def review_route_conflicts(conflicts: list[dict[str, Any]], chapter_number: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        current_chapter = _int_or_none(conflict.get("chapter_current"))
        if current_chapter != chapter_number:
            continue
        conflict_type = str(conflict.get("type") or "route_conflict").strip() or "route_conflict"
        message = str(conflict.get("message") or "").strip()
        if not message:
            continue
        key = f"{conflict_type}:{message}"
        if key in seen:
            continue
        seen.add(key)
        severity = "critical" if str(conflict.get("severity") or "") == "hard" else "warning"
        issue_type = "evolution_route_missing_transition" if conflict_type == "location_jump_without_bridge" else f"evolution_route_{conflict_type}"
        issue = review_issue(
            issue_type,
            severity,
            message,
            chapter_number,
            _route_conflict_suggestion(conflict_type),
        )
        issue["evidence"] = [
            {
                "type": conflict_type,
                "severity": conflict.get("severity"),
                "character": conflict.get("character"),
                "chapter_previous": conflict.get("chapter_previous"),
                "chapter_current": conflict.get("chapter_current"),
                "previous_location": conflict.get("previous_location"),
                "current_location": conflict.get("current_location"),
                "evidence": conflict.get("evidence"),
            }
        ]
        issues.append(issue)
    return issues


def replacement_guidance_for_phrase(phrase: str) -> str:
    if phrase in {"没有说话", "没有回答", "沉默几秒", "沉默了几秒"}:
        return "用手部动作、视线落点、站位变化或物件处理替代沉默模板，并让沉默推动关系或信息差。"
    if phrase in {"喉咙发紧", "深吸一口气", "声音很轻"}:
        return "改用更具体的身体反应、环境压迫或句式节奏，不要重复同一生理模板。"
    return "替换为场景化动作和可观察细节，让反应承担新的剧情信息。"


def _issue_family(issue_type: str) -> str:
    text = str(issue_type or "")
    for marker, family in (
        ("route", "route"),
        ("entity_identity", "entity_identity"),
        ("time_pressure", "time_pressure"),
        ("constraint", "continuity_constraint"),
        ("boundary", "boundary_state"),
        ("palette", "personality_palette"),
        ("pollution", "entity_pollution"),
        ("style_repetition", "style_repetition"),
        ("bible", "bible"),
        ("story_knowledge", "story_knowledge"),
        ("storyline", "storyline"),
        ("foreshadow", "foreshadow"),
        ("timeline", "timeline"),
        ("chronicle", "chronicle"),
        ("dialogue", "dialogue"),
        ("triple", "triples"),
        ("memory_engine", "memory_engine"),
        ("knowledge", "knowledge"),
        ("worldbuilding", "worldbuilding"),
    ):
        if marker in text:
            return family
    return text.replace("evolution_", "") or "general"


def _host_context_mentions(items: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    matches = []
    for item in items:
        if not isinstance(item, dict):
            continue
        terms = [str(item.get("name") or "").strip(), str(item.get("kind") or "").strip()]
        terms.extend(_extract_short_terms(item.get("description")))
        if any(term and len(term) >= 2 and term in text for term in terms[:8]):
            matches.append(item)
    return matches


def _extract_short_terms(value: Any) -> list[str]:
    terms = []
    current = []
    for char in str(value or ""):
        if "\u4e00" <= char <= "\u9fff" or char.isalnum():
            current.append(char)
            continue
        if 2 <= len(current) <= 12:
            terms.append("".join(current))
        current = []
    if 2 <= len(current) <= 12:
        terms.append("".join(current))
    return terms[:6]


def _boundary_issue(issue_type: str, chapter_number: int, description: str, previous: dict[str, Any], opening: str) -> dict[str, Any]:
    issue = review_issue(
        issue_type,
        "critical",
        description,
        chapter_number,
        "必须先重写本章开头100-300字：承接上一章终点；若跳时空，先补移动、撤离、失败、跳时或视角桥接。",
    )
    ending = previous.get("ending_state") if isinstance(previous.get("ending_state"), dict) else {}
    carry = previous.get("carry_forward") if isinstance(previous.get("carry_forward"), dict) else {}
    route_state = carry.get("route_state") if isinstance(carry.get("route_state"), dict) else {}
    if not route_state and isinstance(carry.get("continuity_route_state"), dict):
        route_state = carry.get("continuity_route_state")
    character_positions = carry.get("character_positions") if isinstance(carry.get("character_positions"), dict) else {}
    previous_ending = str(ending.get("excerpt") or "")[:220]
    current_opening = str(opening or "")[:220]
    bridge_type = _required_bridge_type(issue_type, opening)
    issue["evidence"] = [
        {
            "previous_chapter": previous.get("chapter_number"),
            "previous_ending": previous_ending,
            "current_opening": current_opening,
        }
    ]
    issue["gate"] = "boundary_continuity"
    issue["revision_required"] = True
    issue["revision_mode"] = "manual_or_host_revision_required"
    issue["opening_revision_brief"] = {
        "target": "rewrite_opening_100_300_chars",
        "previous_chapter": previous.get("chapter_number"),
        "previous_ending_evidence": previous_ending,
        "current_opening_problem": description,
        "required_bridge_type": bridge_type,
        "route_state": route_state,
        "character_positions": character_positions,
        "rewrite_requirements": [
            "开头先处理上一章结尾地点、尾钩、即时威胁和最后动作。",
            "同时兑现上一章路线状态：起点/终点、移动方式、耗时、同行或分离人物。",
            "若有人受伤、被追踪、携带关键物件或处于倒计时，开头必须交代其状态变化。",
            f"采用{bridge_type}，让读者看见因果桥后再进入本章新场景。",
            "不要重置为首次抵达；不要跳过未完成目标或句子未完的尾钩。",
        ],
        "preserve_after_opening": "保留当前章节后续有效内容，只替换或补写开头承接段。",
    }
    return issue


def _extract_boundary_locations(text: str) -> list[str]:
    found = [marker for marker in BOUNDARY_LOCATION_MARKERS if marker in text]
    generic = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_-]{0,8}(?:机房|礼堂|后台|塔顶|水箱|电梯井|消防梯|安全屋|控制室|锅炉房|走廊|通道|门口|舞台|支柱)", text or "")
    return _dedupe([*found, *generic])[:8]


def _route_state_requires_bridge(route_state: dict[str, Any], opening: str, previous_locations: list[str], current_locations: list[str]) -> bool:
    end_location = str(route_state.get("end_location") or "").strip()
    if not end_location:
        return False
    if end_location in str(opening or ""):
        return False
    if _has_boundary_bridge(opening, previous_locations=previous_locations, current_locations=current_locations):
        return False
    if any(location and location in str(opening or "") for location in previous_locations[:4]):
        return False
    if any(token in str(opening or "") for token in ("回到", "来到", "抵达", "进入", "推开", "走进", "站在")):
        return True
    return bool(current_locations)


def _character_state_issue(character_positions: dict[str, Any], opening: str) -> str:
    if not isinstance(character_positions, dict) or not character_positions:
        return ""
    text = str(opening or "")
    missing = []
    stateful = []
    for name, state in list(character_positions.items())[:4]:
        if str(name) and str(name) not in text:
            missing.append(str(name))
        state_text = str(state.get("state") or "") if isinstance(state, dict) else str(state or "")
        if any(marker in state_text for marker in CHARACTER_STATE_MARKERS):
            if not any(marker in text for marker in CHARACTER_STATE_MARKERS) and not any(term in text for term in _split_terms(state_text)[:6]):
                stateful.append(str(name))
    if stateful:
        return f"上一章人物状态（伤势、携带物、追踪或被困）未在本章开头交代：{', '.join(stateful[:4])}。"
    if len(missing) >= 2 and not _has_boundary_bridge(text):
        return f"上一章在场人物无交代地从本章开头消失：{', '.join(missing[:4])}。"
    return ""


def _has_boundary_bridge(text: str, *, previous_locations: Optional[list[str]] = None, current_locations: Optional[list[str]] = None) -> bool:
    value = str(text or "")
    previous_locations = previous_locations or []
    current_locations = current_locations or []
    if any(marker in value for marker in BOUNDARY_BRIDGE_MARKERS):
        return True
    movement = r"(?:赶往|抵达|来到|进入|回到|转入|撤到|退到|逃到|绕路到|转移到|移动到)"
    if re.search(rf"从.{{1,32}}{movement}.{{1,32}}", value):
        return True
    if re.search(r"(?:沿着|穿过|绕过|经由|顺着).{1,32}(?:来到|抵达|进入|回到|赶往|撤到|退到).{1,32}", value):
        return True
    if previous_locations and current_locations:
        previous_pattern = "|".join(re.escape(item) for item in previous_locations[:4] if item)
        current_pattern = "|".join(re.escape(item) for item in current_locations[:4] if item)
        if previous_pattern and current_pattern and re.search(rf"(?:从|离开|撤离|退出).{{0,16}}(?:{previous_pattern}).{{0,40}}(?:到|赶往|抵达|进入|转入|撤到).{{0,16}}(?:{current_pattern})", value):
            return True
    if re.search(r"(?:几分钟|半小时|数小时|一夜|第二天|天亮后|夜色压下来).{0,24}(?:后|之后|过去|抵达|回到|来到)", value):
        return True
    if re.search(r"(?:没能|失败|中断|被打断|被迫|不得不).{0,24}(?:离开|撤离|改道|退回|转移)", value):
        return True
    return False


def _boundary_terms_handled(opening: str, value: str) -> bool:
    text = str(opening or "")
    terms = [term for term in _split_terms(value) if len(term) >= 2]
    if not terms:
        return _has_boundary_bridge(text)
    if any(term in text for term in terms[:8]):
        return True
    consequence_markers = ("蓝光", "爆发", "后果", "反噬", "门开", "门后", "呼吸声", "徽章", "录音", "封条")
    if any(marker in text for marker in consequence_markers) and any(marker in text for marker in ("撤离", "拽离", "拉开", "退开", "逃离", "被迫", "中断", "爆发")):
        return True
    return _has_boundary_bridge(text) and any(marker in text for marker in ("失败", "没能", "中断", "撤离", "被迫", "放弃", "改道", "回头", "拽离", "退开"))


def _required_bridge_type(issue_type: str, opening: str) -> str:
    text = str(opening or "")
    if issue_type == "evolution_route_missing_transition":
        return "路线桥接/耗时桥接/撤离桥接"
    if issue_type == "evolution_character_state_drop":
        return "人物状态承接/分离交代"
    if issue_type == "evolution_unresolved_cliffhanger_skip":
        return "原地续接/撤离/被打断"
    if "撤离" in text or "逃离" in text or "退回" in text:
        return "撤离桥接"
    if issue_type == "evolution_boundary_goal_skip":
        return "目标继续/失败/改道"
    return "移动桥接/视角桥接"


def _sample_phrase_context(content: str, phrase: str) -> str:
    text = str(content or "")
    index = text.find(phrase)
    if index < 0:
        return ""
    return text[max(0, index - 50) : index + len(phrase) + 50]


def _route_conflict_suggestion(conflict_type: str) -> str:
    if conflict_type == "repeated_arrival":
        return "如果角色上一章结尾已经在该地点，本章开头应承接在场状态；若要重新进入，请补足离开、转场和再次抵达的因果。"
    if conflict_type == "location_jump_without_bridge":
        return "补写移动桥段、跳时提示或视角切换，让读者知道角色如何从上一地点到达当前地点。"
    if conflict_type == "missing_transition":
        return "补写移动桥段、跳时提示或视角切换，让读者知道角色如何从上一地点到达当前地点。"
    if conflict_type == "boundary_rollback":
        return "承接上一章终点；如果回到旧地点，必须先交代离开与再次抵达。"
    if conflict_type == "multi_location_same_chapter":
        return "明确同章内的移动顺序和时间间隔，避免同一角色像同时存在于多个地点。"
    return "核对人物上一章终点、本章起点和场景移动链，补足必要过渡。"


def _missing_palette_fields(palette: Any) -> list[str]:
    if not isinstance(palette, dict):
        return ["base", "main_tones", "derivatives"]
    missing = []
    if not str(palette.get("base") or "").strip():
        missing.append("base")
    if not palette.get("main_tones"):
        missing.append("main_tones")
    if not palette.get("derivatives"):
        missing.append("derivatives")
    return missing


def _looks_like_palette_drift(content: str) -> bool:
    text = str(content or "")
    return any(token in text for token in ("突然变得", "一反常态", "完全不像自己", "像换了个人", "毫无理由地"))


def _as_strings(items: Any) -> list[str]:
    return [str(item or "").strip() for item in (items or []) if str(item or "").strip()]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _mentions_key_terms(content: str, phrase: str) -> bool:
    terms = [term for term in _split_terms(phrase) if len(term) >= 2]
    terms.extend(_semantic_terms(phrase))
    terms = list(dict.fromkeys(terms))
    if not terms:
        return phrase in content
    if any(len(term) >= 4 and term in content for term in terms):
        return True
    matches = sum(1 for term in terms if term in content)
    return matches >= min(2, len(terms))


def _semantic_terms(phrase: str) -> list[str]:
    cleaned = phrase
    for marker in ("不能", "无法", "不会", "不知", "不知道", "凭空", "直接", "轻易", "所有"):
        cleaned = cleaned.replace(marker, "")
    return [cleaned[index : index + 4] for index in range(0, max(len(cleaned) - 3, 0)) if cleaned[index : index + 4].strip()]


def _looks_resolved_without_transition(content: str, unknown: str) -> bool:
    return _mentions_key_terms(content, unknown) and _has_knowledge_marker(content) and not _has_transition_marker(content)


def _split_terms(text: str) -> list[str]:
    separators = "，。；、：:（）()【】[]《》 \n\t"
    current = text
    for sep in separators:
        current = current.replace(sep, "|")
    terms = []
    for part in current.split("|"):
        part = part.strip()
        if not part:
            continue
        if len(part) > 8:
            terms.extend(part[index : index + 4] for index in range(0, len(part), 4))
        else:
            terms.append(part)
    return terms


def _has_knowledge_marker(content: str) -> bool:
    markers = ["知道", "明白", "清楚", "意识到", "看穿", "断定", "确定", "早就", "原来"]
    return any(marker in content for marker in markers)


def _has_mastery_marker(content: str) -> bool:
    markers = ["轻易", "立刻", "毫不费力", "随手", "直接", "精准", "完全", "熟练", "一眼", "看穿"]
    return any(marker in content for marker in markers)


def _has_all_knowing_marker(content: str) -> bool:
    markers = ["一切都在", "早已算到", "全都知道", "早就知道", "毫无疑问", "不用验证"]
    return any(marker in content for marker in markers)


def _has_transition_marker(content: str) -> bool:
    markers = [
        "发现",
        "意识到",
        "终于明白",
        "从",
        "得知",
        "听见",
        "看见",
        "试探",
        "验证",
        "推断",
        "猜测",
        "误以为",
        "代价",
        "失败",
        "受伤",
        "请教",
        "提醒",
        "线索",
        "证据",
    ]
    return any(marker in content for marker in markers)


def _int_or_none(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
