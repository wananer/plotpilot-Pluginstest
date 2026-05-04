"""Chapter state summaries and deterministic continuity checks."""
from __future__ import annotations

import re
from typing import Any


DEFAULT_CHARACTERS = ["沈砚", "林照", "陈建平", "顾岚", "陆行舟", "沈澜", "顾珩", "圣像", "许衡", "许念", "李雯", "影子", "镜师", "林主任"]
AMBIGUOUS_CHARACTER_NAMES = {"影子", "圣像"}
AMBIGUOUS_CHARACTER_CONTEXT = {
    "影子": ("追踪", "追", "跟踪", "手电", "脚步", "逼近", "盯着", "抓", "袭击", "拦住", "说", "问", "回答"),
    "圣像": ("说", "问", "回答", "醒", "移动", "转头", "注视", "伸手", "追", "逼近"),
}
AMBIGUOUS_NON_CHARACTER_CONTEXT = {
    "影子": ("斑驳的影子", "投下", "投在", "倒影", "阴影", "光影", "树影", "影子像"),
    "圣像": ("石膏", "雕像", "墙上", "供桌", "裂纹"),
}
TRACKED_OBJECTS = ["黑匣子", "照片", "警徽", "笔记本", "石板", "石灰圈", "临时平板", "临时卡片", "访客卡", "臂章", "金属盒子", "探测器", "读取器", "便携式脑波扫描仪", "金属盒", "平板", "手机", "晶体", "U盘"]
LOCATION_MARKERS = [
    "石室门口",
    "地下石室",
    "下层石室",
    "封存石室",
    "封存档案室",
    "石室",
    "戒律堂",
    "照影山",
    "C307",
    "宿舍区",
    "C区避难点",
    "监察处",
    "礼堂",
    "旧设备区",
    "E-07电梯井",
    "电梯井",
    "废弃储藏室",
    "观测平台",
    "主楼顶层",
    "塔顶",
    "天线阵列",
    "C3节点",
    "档案库",
    "设备间",
    "潮汐机房",
    "机械工坊",
    "实验楼",
    "水箱",
    "雾港学院",
    "雾港",
    "学院",
    "地下大厅",
    "档案馆侧门",
    "档案馆",
    "城市记忆存储站03号节点",
    "废弃工厂",
    "回收站",
    "D区",
    "中枢大楼",
]
BROAD_LOCATIONS = {"学院", "雾港", "宿舍区"}
ARRIVAL_WORDS = ("才找到", "第一次找到", "终于找到", "找到", "走到", "来到", "抵达", "进入", "推开", "刷开")
LEAVE_WORDS = ("离开", "走出", "退回", "回到", "转身朝", "前往")
BRIDGE_WORDS = ("承接", "继续", "原地", "退回", "撤离", "沿着", "穿过", "返回", "被带", "被迫", "昏迷", "醒来", "几个小时后", "天亮", "转入", "视角")
ROUTE_BRIDGE_WORDS = BRIDGE_WORDS + ("赶往", "追到", "逃到", "爬出", "钻进", "绕到", "带到", "送到", "抬到", "拖进", "坠入", "摔进", "分头", "汇合", "路上", "途中")
STATE_WORDS = ("受伤", "流血", "昏迷", "被追", "追踪", "倒计时", "携带", "拿着", "握着", "塞进", "背包", "口袋", "坠入", "跌入", "被困")
THREAT_WORDS = ("威胁", "危险", "危机", "尾钩", "窸窣", "脚步", "刮擦", "黑影", "人影", "声音", "午夜", "醒", "爬行", "逼近", "锁", "关在", "封死", "叹息")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s+|\n+")
_TIME_RE = re.compile(
    r"(?:\d{1,2}:\d{2}|[零一二三四五六七八九十两0-9]+(?:分钟|小时|天|年)前|"
    r"十年前|三天前|今天|明天|昨天|昨晚|今晚|夜间|早上|清晨|上午|中午|下午|傍晚|晚上|"
    r"演习(?:开始|结束|期间)?|第[一二三四五六七八九十0-9]+(?:段|章|天))"
)
_PROFILE_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{2,4})[，,、\s]*"
    r"(?:(?P<age>[一二三四五六七八九十两0-9]{1,3})岁)?[，,、\s]*"
    r"(?P<role>[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:学生|研究生|老师|教授|工程师|医生|警员|记者|主任|学院|专业|系))"
)
_DEADLINE_RE = re.compile(
    r"(最多|只剩|剩下|还有|必须在|限时|倒计时|不超过)?\s*"
    r"(?P<num>四十八|二十四|七十二|一|两|二|三|四|五|六|七|八|九|十|[0-9]{1,3})\s*"
    r"(?P<unit>小时|天|日)"
)


def build_chapter_summary(novel_id: str, chapter_number: int, content: str, at: str) -> dict[str, Any]:
    """Build a compact chapter summary for future context injection."""
    sentences = _sentences(content)
    opening = _window(content, head=True)
    ending = _window(content, head=False)
    opening_state = extract_state(opening, full_text=content)
    ending_state = extract_state(ending, full_text=content)
    chapter_state = extract_state(content, full_text=content)
    return {
        "schema_version": 1,
        "novel_id": novel_id,
        "chapter_number": chapter_number,
        "summary_type": "chapter",
        "short_summary": _short_summary(sentences),
        "opening_state": opening_state,
        "ending_state": ending_state,
        "chapter_state": chapter_state,
        "carry_forward": _carry_forward(opening_state, ending_state, chapter_state, sentences),
        "open_threads": _open_threads(sentences),
        "at": at,
    }


def build_chapter_execution_draft(
    novel_id: str,
    chapter_number: int,
    outline: str,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the pre-generation state contract for one chapter."""
    previous_summary = previous_summary if isinstance(previous_summary, dict) else {}
    carry = previous_summary.get("carry_forward") if isinstance(previous_summary.get("carry_forward"), dict) else {}
    boundary = carry.get("boundary_state") if isinstance(carry.get("boundary_state"), dict) else {}
    previous_locations = _as_strings(carry.get("last_known_locations"))
    previous_characters = _as_strings(carry.get("onscreen_characters"))
    route_state = carry.get("route_state") if isinstance(carry.get("route_state"), dict) else {}
    character_positions = carry.get("character_positions") if isinstance(carry.get("character_positions"), dict) else {}
    time_state = carry.get("time_state") if isinstance(carry.get("time_state"), dict) else {}
    continuity_constraints = carry.get("continuity_constraints")
    if not isinstance(continuity_constraints, list):
        continuity_constraints = build_continuity_constraints_from_summary(previous_summary)
    outline_state = extract_state(outline, full_text=outline)
    outline_locations = _as_strings(outline_state.get("locations"))
    outline_characters = _as_strings(outline_state.get("characters"))
    start_location = str(boundary.get("ending_location") or _last(previous_locations) or _last(outline_locations) or "").strip()
    onsite = _dedupe([*previous_characters, *outline_characters])[:8]
    immediate_threat = str(boundary.get("immediate_threat") or "").strip()
    open_threads = _as_strings(carry.get("open_threads"))
    opening_bridge = str(boundary.get("required_next_opening") or "").strip()
    if not opening_bridge:
        if start_location:
            opening_bridge = f"本章开头从{start_location}原地续接上一章结尾，先处理尾钩/威胁/未完成动作，再推进大纲。"
        else:
            opening_bridge = "本章开头必须先兑现上一章结尾；如需换时间或地点，先写清桥接原因。"
    return {
        "schema_version": 1,
        "novel_id": novel_id,
        "chapter_number": chapter_number,
        "source": "deterministic_boundary_contract",
        "timepoint": str(carry.get("last_known_time") or boundary.get("timepoint") or "紧接上一章").strip(),
        "time_delta": str(time_state.get("required_next_time_delta") or "紧接上一章；若跳时必须先写明经过多久和为何跳时").strip(),
        "start_location": start_location,
        "expected_end_location": _last(outline_locations),
        "onscreen_characters": onsite,
        "offstage_characters": [],
        "opening_bridge": opening_bridge,
        "opening_route_bridge": _opening_route_bridge(route_state, start_location),
        "character_positions": character_positions,
        "separation_state": _separation_state(previous_characters, character_positions),
        "required_evidence_terms": _required_evidence_terms(boundary, route_state, character_positions),
        "continuity_constraints": continuity_constraints,
        "continuity_constraint_state": {"items": continuity_constraints},
        "chapter_goal": _chapter_goal_from_outline(outline, open_threads),
        "immediate_threat": immediate_threat,
        "key_object_states": carry.get("object_states") or [],
        "previous_boundary_state": boundary,
        "status": "locked",
        "revision_attempts": 0,
    }


def repair_chapter_execution_draft(draft: dict[str, Any], previous_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministically repair a draft so prior chapter boundary wins over outline drift."""
    repaired = dict(draft)
    previous_summary = previous_summary if isinstance(previous_summary, dict) else {}
    carry = previous_summary.get("carry_forward") if isinstance(previous_summary.get("carry_forward"), dict) else {}
    boundary = carry.get("boundary_state") if isinstance(carry.get("boundary_state"), dict) else {}
    previous_locations = _as_strings(carry.get("last_known_locations"))
    previous_characters = _as_strings(carry.get("onscreen_characters"))
    route_state = carry.get("route_state") if isinstance(carry.get("route_state"), dict) else {}
    character_positions = carry.get("character_positions") if isinstance(carry.get("character_positions"), dict) else {}
    time_state = carry.get("time_state") if isinstance(carry.get("time_state"), dict) else {}
    start_location = str(boundary.get("ending_location") or _last(previous_locations) or repaired.get("start_location") or "").strip()
    if start_location:
        repaired["start_location"] = start_location
    if previous_characters:
        repaired["onscreen_characters"] = _dedupe([*previous_characters, *_as_strings(repaired.get("onscreen_characters"))])[:8]
    threat = str(boundary.get("immediate_threat") or repaired.get("immediate_threat") or "").strip()
    if threat:
        repaired["immediate_threat"] = threat
    required = str(boundary.get("required_next_opening") or "").strip()
    if required:
        repaired["opening_bridge"] = required
    elif start_location:
        repaired["opening_bridge"] = f"从{start_location}原地续接上一章结尾，先兑现即时威胁和未完成目标，再进入本章大纲。"
    repaired["opening_route_bridge"] = _opening_route_bridge(route_state, start_location)
    repaired["character_positions"] = character_positions
    repaired["separation_state"] = _separation_state(previous_characters, character_positions)
    repaired["time_delta"] = str(time_state.get("required_next_time_delta") or repaired.get("time_delta") or "紧接上一章").strip()
    repaired["required_evidence_terms"] = _required_evidence_terms(boundary, route_state, character_positions)
    constraints = carry.get("continuity_constraints")
    if not isinstance(constraints, list):
        constraints = build_continuity_constraints_from_summary(previous_summary)
    repaired["continuity_constraints"] = constraints
    repaired["continuity_constraint_state"] = {"items": constraints}
    repaired["status"] = "auto_revised"
    repaired["revision_attempts"] = int(repaired.get("revision_attempts") or 0) + 1
    return repaired


def review_chapter_execution_draft(
    previous_summary: dict[str, Any] | None,
    draft: dict[str, Any],
    chapter_number: int,
) -> list[dict[str, Any]]:
    previous_summary = previous_summary if isinstance(previous_summary, dict) else {}
    carry = previous_summary.get("carry_forward") if isinstance(previous_summary.get("carry_forward"), dict) else {}
    boundary = carry.get("boundary_state") if isinstance(carry.get("boundary_state"), dict) else {}
    if not carry and not boundary:
        return []
    issues: list[dict[str, Any]] = []
    previous_locations = _dedupe([str(boundary.get("ending_location") or ""), *_as_strings(carry.get("last_known_locations"))])
    start_location = str(draft.get("start_location") or "").strip()
    bridge = str(draft.get("opening_bridge") or "").strip()
    route_bridge = str(draft.get("opening_route_bridge") or "").strip()
    if previous_locations and start_location and start_location not in previous_locations and not any(word in bridge for word in BRIDGE_WORDS):
        issues.append(_draft_issue("chapter_draft_location_conflict", chapter_number, "章前草稿起始地点没有承接上一章结尾，也缺少移动/跳时桥接。", previous_summary, draft))
    if previous_locations and not start_location:
        issues.append(_draft_issue("chapter_draft_missing_start_location", chapter_number, "章前草稿缺少起始地点，无法锁定本章开头空间。", previous_summary, draft))
    previous_characters = set(_as_strings(carry.get("onscreen_characters")))
    draft_characters = set(_as_strings(draft.get("onscreen_characters")))
    if previous_characters and not (previous_characters & draft_characters):
        issues.append(_draft_issue("chapter_draft_character_drop", chapter_number, "章前草稿没有保留上一章结尾在场人物。", previous_summary, draft))
    threat = str(boundary.get("immediate_threat") or boundary.get("unresolved_cliffhanger") or "").strip()
    draft_threat = str(draft.get("immediate_threat") or draft.get("opening_bridge") or "").strip()
    if threat and not _boundary_terms_handled_for_draft(draft_threat, threat):
        issues.append(_draft_issue("chapter_draft_threat_skip", chapter_number, "章前草稿没有处理上一章即时威胁或尾钩。", previous_summary, draft))
    route_state = carry.get("route_state") if isinstance(carry.get("route_state"), dict) else {}
    if route_state and previous_locations and route_state.get("end_location") and not route_bridge:
        issues.append(_draft_issue("chapter_draft_missing_route_bridge", chapter_number, "章前草稿缺少路线桥接说明，无法解释本章如何从上一章终点继续。", previous_summary, draft))
    constraints = draft.get("continuity_constraints")
    if isinstance(constraints, list):
        missing_required = [
            item for item in constraints
            if isinstance(item, dict) and item.get("severity") == "blocking" and not str(item.get("repair_hint") or "").strip()
        ]
        if missing_required:
            issues.append(_draft_issue("chapter_draft_constraint_incomplete", chapter_number, "章前草稿包含高优先级统一约束，但缺少修复/兑现提示。", previous_summary, draft))
    return issues[:4]


def review_execution_draft_against_content(
    draft: dict[str, Any],
    content: str,
    chapter_number: int,
    *,
    opening_chars: int = 800,
) -> list[dict[str, Any]]:
    if not isinstance(draft, dict) or not str(content or "").strip():
        return []
    opening = _clean(str(content or "")[:opening_chars])
    issues: list[dict[str, Any]] = []
    start_location = str(draft.get("start_location") or "").strip()
    if start_location and start_location not in opening and not _has_any_bridge(opening):
        issues.append(_content_draft_issue("chapter_draft_location_unfulfilled", chapter_number, "正文开头没有兑现章前草稿的起始地点，也缺少清晰桥接。", draft, opening))
    characters = _as_strings(draft.get("onscreen_characters"))
    if characters and not any(name in opening for name in characters[:4]):
        issues.append(_content_draft_issue("chapter_draft_characters_unfulfilled", chapter_number, "正文开头没有兑现章前草稿的在场人物。", draft, opening))
    threat = str(draft.get("immediate_threat") or "").strip()
    if threat and not _boundary_terms_handled_for_draft(opening, threat) and not _has_any_bridge(opening):
        issues.append(_content_draft_issue("chapter_draft_threat_unfulfilled", chapter_number, "正文开头没有兑现章前草稿的即时威胁/尾钩。", draft, opening))
    route_bridge = str(draft.get("opening_route_bridge") or "").strip()
    required_terms = _as_strings(draft.get("required_evidence_terms"))
    if route_bridge and not _route_bridge_fulfilled(opening, route_bridge, required_terms):
        issues.append(_content_draft_issue("chapter_route_bridge_unfulfilled", chapter_number, "正文开头没有兑现章前路线桥接，跨地点/跨时间移动缺少可见因果链。", draft, opening))
    character_positions = draft.get("character_positions") if isinstance(draft.get("character_positions"), dict) else {}
    if character_positions and not _character_state_fulfilled(opening, character_positions):
        issues.append(_content_draft_issue("chapter_character_state_unfulfilled", chapter_number, "正文开头没有交代上一章人物位置、伤势、携带物或追踪状态。", draft, opening))
    constraints = draft.get("continuity_constraints")
    if isinstance(constraints, list):
        issues.extend(review_continuity_constraints_against_content({"carry_forward": {"continuity_constraints": constraints}}, opening, chapter_number))
    return issues[:4]


def render_chapter_execution_draft(draft: dict[str, Any]) -> str:
    if not isinstance(draft, dict) or not draft:
        return ""
    lines = [
        "【章前状态草稿硬约束】",
        "本章正文必须先兑现以下状态草稿；前100-300字优先处理 opening_bridge，不得直接跳场或重置人物状态。",
    ]
    mapping = [
        ("timepoint", "时间点"),
        ("time_delta", "时间差"),
        ("start_location", "起始地点"),
        ("expected_end_location", "预期结束地点"),
        ("onscreen_characters", "在场人物"),
        ("offstage_characters", "缺席人物"),
        ("opening_bridge", "开头承接动作"),
        ("opening_route_bridge", "路线桥接"),
        ("separation_state", "人物分离/同行状态"),
        ("chapter_goal", "本章目标"),
        ("immediate_threat", "即时威胁"),
        ("required_evidence_terms", "必须出现的承接证据词"),
    ]
    for key, label in mapping:
        value = draft.get(key)
        if isinstance(value, list):
            text = "、".join(_as_strings(value))
        else:
            text = str(value or "").strip()
        if text:
            lines.append(f"- {label}：{text[:240]}")
    objects = _render_object_states_for_draft(draft.get("key_object_states"))
    if objects:
        lines.append(f"- 关键物件状态：{objects}")
    positions = _render_character_positions(draft.get("character_positions"))
    if positions:
        lines.append(f"- 人物位置状态：{positions}")
    constraints = render_continuity_constraints(draft.get("continuity_constraints"))
    if constraints:
        lines.append(constraints)
    lines.append("- 禁止：先写新地点/新时间，再回头解释上一章危机；禁止把上一章在场人物无交代地移出场。")
    return "\n".join(lines)


def build_continuity_constraint_state(
    constraint_type: str,
    scope: str,
    anchor: str,
    evidence: Any,
    severity: str,
    repair_hint: str,
    confidence: float = 0.8,
) -> dict[str, Any]:
    """Build the generic continuity constraint shape used by drafts, gates and reports."""
    normalized_evidence = evidence if isinstance(evidence, list) else [evidence] if evidence else []
    return {
        "constraint_type": str(constraint_type or "continuity").strip(),
        "scope": str(scope or "next_chapter_opening").strip(),
        "anchor": str(anchor or "").strip(),
        "evidence": normalized_evidence,
        "severity": str(severity or "warning").strip(),
        "repair_hint": str(repair_hint or "").strip(),
        "confidence": max(0.0, min(float(confidence or 0.0), 1.0)),
    }


def build_continuity_constraints_from_summary(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    carry = summary.get("carry_forward") if isinstance(summary.get("carry_forward"), dict) else {}
    boundary = carry.get("boundary_state") if isinstance(carry.get("boundary_state"), dict) else {}
    route_state = carry.get("route_state") if isinstance(carry.get("route_state"), dict) else {}
    character_positions = carry.get("character_positions") if isinstance(carry.get("character_positions"), dict) else {}
    constraints: list[dict[str, Any]] = []

    end_location = str(route_state.get("end_location") or boundary.get("ending_location") or "").strip()
    if end_location:
        constraints.append(build_continuity_constraint_state(
            "location_transition",
            "next_chapter_opening",
            end_location,
            [{"route_state": route_state, "boundary_state": boundary}],
            "blocking",
            f"下一章开头必须从{end_location}承接；若换地点，先写移动路径、耗时、撤离、失败或视角桥接。",
            0.9,
        ))

    for name, state in list(character_positions.items())[:6]:
        if not str(name).strip():
            continue
        state_text = str(state.get("state") or "") if isinstance(state, dict) else str(state or "")
        location = str(state.get("location") or "") if isinstance(state, dict) else ""
        constraints.append(build_continuity_constraint_state(
            "character_state",
            "next_chapter_opening",
            str(name),
            [{"name": name, "state": state, "location": location}],
            "blocking" if any(word in state_text for word in STATE_WORDS) else "warning",
            f"交代{name}在{location or '上一章终点'}的去向、是否继续在场，以及{state_text or '在场'}状态如何变化。",
            0.82,
        ))

    for item in boundary.get("key_object_states") or carry.get("object_states") or []:
        if not isinstance(item, dict):
            continue
        obj = str(item.get("object") or "").strip()
        if not obj:
            continue
        constraints.append(build_continuity_constraint_state(
            "object_state",
            "next_chapter_opening",
            obj,
            [item],
            "warning",
            f"若{obj}继续影响行动，开头需交代它由谁持有、是否丢失/转交/损坏。",
            0.76,
        ))

    threat = str(boundary.get("immediate_threat") or boundary.get("unresolved_cliffhanger") or "").strip()
    if threat:
        constraints.append(build_continuity_constraint_state(
            "threat_state",
            "next_chapter_opening",
            threat[:80],
            [{"boundary_state": boundary}],
            "blocking",
            "先兑现上一章即时威胁/尾钩：原地处理、撤离、失败、被打断或写出后果。",
            0.86,
        ))

    goal = str(boundary.get("active_goal") or "").strip()
    if goal:
        constraints.append(build_continuity_constraint_state(
            "goal_state",
            "next_chapter_opening",
            goal[:80],
            [{"boundary_state": boundary}],
            "warning",
            "开头需说明上一章未完成目标是继续、失败、中断还是改道。",
            0.72,
        ))

    source_text = _summary_text(summary)
    for profile in _extract_entity_profiles(source_text):
        constraints.append(build_continuity_constraint_state(
            "entity_identity",
            "chapter_chain",
            profile["name"],
            [profile],
            "blocking",
            f"保持实体身份一致：{profile['name']}的年龄/身份/专业不得静默改名或改设定；如是假名/误认，正文必须明示。",
            0.78,
        ))

    for deadline in _extract_deadlines(source_text):
        constraints.append(build_continuity_constraint_state(
            "time_pressure",
            "chapter_chain",
            deadline["text"],
            [deadline],
            "blocking",
            f"保持期限压力：{deadline['text']}不得被静默放宽；如延期，必须写出明确原因和代价。",
            0.8,
        ))

    return _dedupe_constraints(constraints)[:16]


def render_continuity_constraints(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return ""
    lines = ["【统一连续性约束】", "前100-300字优先兑现 blocking 约束；warning 约束也需给出可见证据或桥接。"]
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("constraint_type") or "continuity")
        severity = str(item.get("severity") or "warning")
        anchor = str(item.get("anchor") or "").strip()
        hint = str(item.get("repair_hint") or "").strip()
        lines.append(f"- {severity}/{kind}：{anchor}；{hint[:180]}")
    return "\n".join(lines)


def review_continuity_constraints_against_content(
    previous_summary: dict[str, Any] | None,
    content: str,
    chapter_number: int,
    *,
    opening_chars: int = 800,
) -> list[dict[str, Any]]:
    constraints = []
    if isinstance(previous_summary, dict):
        carry = previous_summary.get("carry_forward") if isinstance(previous_summary.get("carry_forward"), dict) else {}
        existing = carry.get("continuity_constraints")
        constraints = existing if isinstance(existing, list) else build_continuity_constraints_from_summary(previous_summary)
    if not constraints:
        return []
    opening = _clean(str(content or "")[:opening_chars])
    issues: list[dict[str, Any]] = []
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        kind = str(constraint.get("constraint_type") or "")
        if kind == "entity_identity":
            issue = _review_entity_identity_constraint(constraint, opening, chapter_number)
        elif kind == "time_pressure":
            issue = _review_time_pressure_constraint(constraint, opening, chapter_number)
        elif kind == "location_transition":
            issue = _review_location_transition_constraint(constraint, opening, chapter_number)
        elif kind == "character_state":
            issue = _review_character_constraint(constraint, opening, chapter_number)
        elif kind in {"object_state", "goal_state", "threat_state"}:
            issue = _review_anchor_or_bridge_constraint(constraint, opening, chapter_number)
        else:
            issue = {}
        if issue:
            issues.append(issue)
    return issues[:6]


def build_volume_summary(novel_id: str, volume_index: int, chapter_summaries: list[dict[str, Any]], at: str) -> dict[str, Any]:
    """Build a larger summary for each 10-chapter block."""
    chapters = sorted(chapter_summaries, key=lambda item: int(item.get("chapter_number") or 0))
    start = int(chapters[0].get("chapter_number") or 0) if chapters else (volume_index - 1) * 10 + 1
    end = int(chapters[-1].get("chapter_number") or 0) if chapters else volume_index * 10
    unresolved: list[str] = []
    places: list[str] = []
    characters: list[str] = []
    lines = []
    for item in chapters:
        chapter_number = item.get("chapter_number")
        summary = _clean(item.get("short_summary") or "")
        if summary:
            lines.append(f"第{chapter_number}章：{summary}")
        carry = item.get("carry_forward") if isinstance(item.get("carry_forward"), dict) else {}
        unresolved.extend(_as_strings(carry.get("open_threads")))
        ending = item.get("ending_state") if isinstance(item.get("ending_state"), dict) else {}
        places.extend(_as_strings(ending.get("locations")))
        characters.extend(_as_strings(ending.get("characters")))
    return {
        "schema_version": 1,
        "novel_id": novel_id,
        "summary_type": "volume",
        "volume_index": volume_index,
        "chapter_start": start,
        "chapter_end": end,
        "short_summary": "；".join(lines)[-1200:],
        "main_locations": _dedupe(places)[-8:],
        "main_characters": _dedupe(characters)[-12:],
        "open_threads": _dedupe(unresolved)[-10:],
        "at": at,
    }


def analyze_chapter_transitions(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    states = []
    conflicts: list[dict[str, Any]] = []
    memory: dict[str, Any] = {"objects": {}, "visited_locations": set(), "flags": set()}
    sorted_chapters = sorted(chapters, key=lambda item: int(item.get("chapter_number") or 0))
    previous: dict[str, Any] | None = None
    for chapter in sorted_chapters:
        content = str(chapter.get("content") or "")
        chapter_number = int(chapter.get("chapter_number") or 0)
        state = build_chapter_summary("", chapter_number, content, "")
        states.append(state)
        if previous:
            conflicts.extend(_compare_adjacent(previous, state, memory))
        conflicts.extend(_compare_memory(state, memory))
        _update_memory(memory, state)
        previous = state
    return {
        "schema_version": 1,
        "states": states,
        "conflicts": conflicts,
        "aggregate": {
            "conflict_count": len(conflicts),
            "hard_conflict_count": sum(1 for item in conflicts if item.get("severity") == "hard"),
            "warning_count": sum(1 for item in conflicts if item.get("severity") == "warning"),
        },
    }


def extract_state(text: str, *, full_text: str = "") -> dict[str, Any]:
    compact = _clean(text)
    source = full_text or text
    return {
        "excerpt": compact[:360],
        "time_markers": _dedupe(_TIME_RE.findall(compact))[-6:],
        "locations": _extract_locations(compact),
        "characters": _extract_characters(compact, source),
        "object_states": _extract_object_states(compact),
        "actions": _extract_actions(compact),
    }


def _compare_adjacent(previous: dict[str, Any], current: dict[str, Any], memory: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    prev_chapter = int(previous.get("chapter_number") or 0)
    curr_chapter = int(current.get("chapter_number") or 0)
    prev_end = previous.get("ending_state") or {}
    curr_open = current.get("opening_state") or {}
    prev_text = str(prev_end.get("excerpt") or "")
    curr_text = str(curr_open.get("excerpt") or "")
    prev_locs = set(_as_strings(prev_end.get("locations")))
    curr_locs = set(_as_strings(curr_open.get("locations")))

    for loc in sorted((prev_locs | set(memory.get("visited_locations") or set())) & curr_locs):
        if loc in BROAD_LOCATIONS:
            continue
        if _has_arrival_reset(curr_text, loc):
            conflicts.append(
                _conflict(
                    "repeated_arrival",
                    "hard",
                    prev_chapter,
                    curr_chapter,
                    f"第{curr_chapter}章开头把{loc}写成重新/首次抵达，但前文已经到过该地点。",
                    prev_text,
                    curr_text,
                )
            )

    if "演习结束" in prev_text and ("演习期间" in curr_text or "等待广播通知演习结束" in curr_text):
        conflicts.append(
            _conflict(
                "time_rollback",
                "hard",
                prev_chapter,
                curr_chapter,
                "上一章已出现演习结束，下一章开头又回到演习进行中。",
                prev_text,
                curr_text,
            )
        )

    if prev_locs & curr_locs and any(word in curr_text for word in ("刷卡", "推开防火门", "走进", "进入")):
        conflicts.append(
            _conflict(
                "scene_reentry_needs_bridge",
                "warning",
                prev_chapter,
                curr_chapter,
                "相邻章节在同一地点重复进入，应补时间/视角/位置桥接，否则会像状态重置。",
                prev_text,
                curr_text,
            )
        )
    return conflicts


def _compare_memory(current: dict[str, Any], memory: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    chapter_number = int(current.get("chapter_number") or 0)
    opening = current.get("opening_state") or {}
    whole = current.get("chapter_state") or {}
    text = str(opening.get("excerpt") or "")
    object_text = " ".join(str(item.get("snippet") or "") for item in whole.get("object_states") or [] if isinstance(item, dict))
    object_memory = memory.get("objects") if isinstance(memory.get("objects"), dict) else {}

    black_box_state = str(object_memory.get("黑匣子") or "")
    if "锁进" in black_box_state and "抽屉" in black_box_state and "从帆布包里取出黑匣子" in object_text:
        conflicts.append(
            _conflict(
                "object_teleport",
                "hard",
                int(memory.get("last_object_chapter", {}).get("黑匣子") or chapter_number - 1),
                chapter_number,
                "黑匣子前文被锁进抽屉，后文直接从帆布包取出，缺少取回桥段。",
                black_box_state,
                object_text,
            )
        )

    if "entered_archive" in (memory.get("flags") or set()) and "档案库门口" in text and "非授权人员禁止进入" in text:
        conflicts.append(
            _conflict(
                "permission_state_reset",
                "hard",
                int(memory.get("entered_archive_chapter") or chapter_number - 1),
                chapter_number,
                "前文已经进入档案库，后文开头又回到档案库门口刷卡失败。",
                "前文状态：已进入档案库。",
                text,
            )
        )
    return conflicts


def _update_memory(memory: dict[str, Any], state: dict[str, Any]) -> None:
    chapter_number = int(state.get("chapter_number") or 0)
    for key in ("opening_state", "ending_state"):
        section = state.get(key) if isinstance(state.get(key), dict) else {}
        for loc in _as_strings(section.get("locations")):
            memory.setdefault("visited_locations", set()).add(loc)
        text = str(section.get("excerpt") or "")
        if "走进档案库" in text or "进入档案库" in text or "调阅" in text and "档案" in text:
            memory.setdefault("flags", set()).add("entered_archive")
            memory["entered_archive_chapter"] = chapter_number
        for item in section.get("object_states") or []:
            if not isinstance(item, dict):
                continue
            obj = str(item.get("object") or "")
            snippet = str(item.get("snippet") or "")
            if obj and snippet and _is_object_stateful(snippet):
                memory.setdefault("objects", {})[obj] = snippet
                memory.setdefault("last_object_chapter", {})[obj] = chapter_number


def _conflict(kind: str, severity: str, previous_chapter: int, current_chapter: int, message: str, previous_evidence: str, current_evidence: str) -> dict[str, Any]:
    return {
        "type": kind,
        "severity": severity,
        "previous_chapter": previous_chapter,
        "current_chapter": current_chapter,
        "message": message,
        "previous_evidence": _clean(previous_evidence)[:220],
        "current_evidence": _clean(current_evidence)[:220],
    }


def _short_summary(sentences: list[str]) -> str:
    if not sentences:
        return ""
    chosen = sentences[:2]
    if len(sentences) > 2:
        chosen.append(sentences[-1])
    return _clean(" ".join(chosen))[:520]


def _carry_forward(
    opening_state: dict[str, Any],
    ending_state: dict[str, Any],
    chapter_state: dict[str, Any],
    sentences: list[str],
) -> dict[str, Any]:
    boundary = _boundary_state(ending_state, sentences)
    route_state = _route_state(opening_state, ending_state, chapter_state, sentences)
    character_positions = _character_positions(ending_state, route_state)
    time_state = _time_state(opening_state, ending_state, sentences)
    carry = {
        "last_known_time": _last(ending_state.get("time_markers")),
        "last_known_locations": _as_strings(ending_state.get("locations"))[-4:],
        "onscreen_characters": _as_strings(ending_state.get("characters"))[-8:],
        "object_states": ending_state.get("object_states") or [],
        "open_threads": _open_threads(sentences),
        "boundary_state": boundary,
        "route_state": route_state,
        "continuity_route_state": route_state,
        "character_positions": character_positions,
        "time_state": time_state,
        "required_next_bridge": "下一章开头必须承接上一章结尾；若跳过时间或地点，需先交代过渡，避免重复首次抵达、重复开门、物件瞬移。",
    }
    summary_stub = {
        "short_summary": " ".join(sentences[:2] + sentences[-2:]),
        "opening_state": opening_state,
        "ending_state": ending_state,
        "chapter_state": chapter_state,
        "carry_forward": carry,
    }
    constraints = build_continuity_constraints_from_summary(summary_stub)
    carry["continuity_constraints"] = constraints
    carry["continuity_constraint_state"] = {"items": constraints}
    return carry


def _route_state(opening_state: dict[str, Any], ending_state: dict[str, Any], chapter_state: dict[str, Any], sentences: list[str]) -> dict[str, Any]:
    start_location = _last(_as_strings(opening_state.get("locations")))
    end_location = _last(_as_strings(ending_state.get("locations")))
    chapter_text = str(chapter_state.get("excerpt") or "") + " " + " ".join(sentences[-8:])
    return {
        "start_location": start_location,
        "end_location": end_location,
        "movement_method": _movement_method(chapter_text),
        "time_delta": _time_delta(opening_state, ending_state),
        "companions": _as_strings(ending_state.get("characters"))[-8:],
        "separated_characters": _separated_characters(sentences, ending_state),
        "route_evidence": _route_evidence(sentences),
    }


def _time_state(opening_state: dict[str, Any], ending_state: dict[str, Any], sentences: list[str]) -> dict[str, Any]:
    opening_time = _last(_as_strings(opening_state.get("time_markers")))
    ending_time = _last(_as_strings(ending_state.get("time_markers")))
    return {
        "opening_time": opening_time,
        "ending_time": ending_time,
        "chapter_time_delta": _time_delta(opening_state, ending_state),
        "required_next_time_delta": "紧接上一章；若跳时必须先交代经过多久、谁在场、威胁如何变化。",
    }


def _character_positions(ending_state: dict[str, Any], route_state: dict[str, Any]) -> dict[str, Any]:
    location = str(route_state.get("end_location") or "").strip()
    excerpt = str(ending_state.get("excerpt") or "")
    positions = {}
    for name in _as_strings(ending_state.get("characters"))[-8:]:
        positions[name] = {
            "location": location,
            "last_seen": excerpt[:160],
            "state": _character_state_for_excerpt(name, excerpt),
        }
    return positions


def _character_state_for_excerpt(name: str, excerpt: str) -> str:
    local = str(excerpt or "")
    if name and name in local:
        idx = local.find(name)
        local = local[max(0, idx - 80): idx + 160]
    signals = [word for word in STATE_WORDS if word in local]
    return "、".join(_dedupe(signals)) or "在场"


def _movement_method(text: str) -> str:
    for word in ("追", "逃", "撤离", "返回", "沿着", "穿过", "爬", "坠入", "被带", "分头", "汇合", "赶往"):
        if word in text:
            return word
    return ""


def _time_delta(opening_state: dict[str, Any], ending_state: dict[str, Any]) -> str:
    opening_time = _last(_as_strings(opening_state.get("time_markers")))
    ending_time = _last(_as_strings(ending_state.get("time_markers")))
    if opening_time and ending_time and opening_time != ending_time:
        return f"{opening_time} -> {ending_time}"
    return ending_time or opening_time or ""


def _separated_characters(sentences: list[str], ending_state: dict[str, Any]) -> list[str]:
    text = " ".join(sentences[-8:])
    if not any(word in text for word in ("分头", "留下", "离开", "走散", "失踪", "带走")):
        return []
    return _as_strings(ending_state.get("characters"))[-8:]


def _route_evidence(sentences: list[str]) -> list[str]:
    evidence = []
    for sentence in sentences[-8:]:
        if any(word in sentence for word in ROUTE_BRIDGE_WORDS):
            evidence.append(_clean(sentence)[:180])
    return evidence[-3:]


def _opening_route_bridge(route_state: dict[str, Any], start_location: str) -> str:
    if not isinstance(route_state, dict) or not route_state:
        return ""
    end_location = str(route_state.get("end_location") or start_location or "").strip()
    method = str(route_state.get("movement_method") or "").strip()
    evidence = "；".join(_as_strings(route_state.get("route_evidence"))[:2])
    if end_location and method:
        return f"从上一章终点{end_location}承接，先写清{method}后的即时位置、同行/分离人物和威胁变化。"
    if end_location:
        return f"从上一章终点{end_location}承接；若本章要换地点，开头必须先写移动路径、耗时或撤离原因。"
    if evidence:
        return f"承接上一章路线证据：{evidence}"
    return ""


def _separation_state(characters: list[str], character_positions: dict[str, Any]) -> str:
    if not characters:
        return ""
    missing = [name for name in characters if name not in character_positions]
    if missing:
        return f"需交代这些人物是否分离/离场：{'、'.join(missing[:6])}"
    return "上一章在场人物默认继续在场；若分离必须先交代原因。"


def _required_evidence_terms(boundary: dict[str, Any], route_state: dict[str, Any], character_positions: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in (
        boundary.get("ending_location"),
        boundary.get("immediate_threat"),
        boundary.get("active_goal"),
        route_state.get("end_location") if isinstance(route_state, dict) else "",
        route_state.get("movement_method") if isinstance(route_state, dict) else "",
    ):
        terms.extend(_keyword_terms(str(value or ""))[:3])
    for name, state in list(character_positions.items())[:4]:
        terms.append(str(name))
        if isinstance(state, dict):
            terms.extend(_keyword_terms(str(state.get("state") or ""))[:2])
    return _dedupe([term for term in terms if term])[:12]


def _boundary_state(ending_state: dict[str, Any], sentences: list[str]) -> dict[str, Any]:
    ending_locations = _as_strings(ending_state.get("locations"))
    ending_characters = _as_strings(ending_state.get("characters"))
    actions = _as_strings(ending_state.get("actions"))
    ending_excerpt = str(ending_state.get("excerpt") or "").strip()
    threat = _last_threat_sentence(sentences) or _last_question_sentence(sentences)
    active_goal = _last_goal_sentence(sentences)
    cliffhanger = _last_question_sentence(sentences) or threat
    ending_location = _last(ending_locations)
    required = "下一章开头必须"
    if ending_location:
        required += f"从{ending_location}承接"
    else:
        required += "承接上一章结尾"
    if threat:
        required += f"，先处理“{threat[:80]}”"
    elif active_goal:
        required += f"，先推进“{active_goal[:80]}”"
    required += "；如需换时间/地点，先写撤离、移动、失败、被打断、跳时或视角桥接。"
    return {
        "ending_location": ending_location,
        "onscreen_characters": ending_characters[-8:],
        "last_action": _last(actions) or _last(sentences[-3:] if sentences else []),
        "immediate_threat": threat,
        "active_goal": active_goal,
        "unresolved_cliffhanger": cliffhanger,
        "key_object_states": ending_state.get("object_states") or [],
        "ending_excerpt": ending_excerpt[:360],
        "required_next_opening": required,
    }


def _chapter_goal_from_outline(outline: str, open_threads: list[str]) -> str:
    sentences = _sentences(outline)
    if sentences:
        return sentences[0][:220]
    if open_threads:
        return f"继续处理：{open_threads[-1][:180]}"
    return "承接上一章结尾并推进本章主线。"


def _last_threat_sentence(sentences: list[str]) -> str:
    for sentence in reversed(sentences[-8:]):
        if any(word in sentence for word in THREAT_WORDS):
            return sentence[:180]
    return ""


def _last_question_sentence(sentences: list[str]) -> str:
    for sentence in reversed(sentences[-8:]):
        if any(word in sentence for word in ("?", "？", "为什么", "谁", "什么", "怎么")):
            return sentence[:180]
    return ""


def _last_goal_sentence(sentences: list[str]) -> str:
    for sentence in reversed(sentences[-8:]):
        if any(word in sentence for word in ("要", "必须", "寻找", "追", "调查", "打开", "确认", "弄清", "阻止")):
            return sentence[:180]
    return ""


def _draft_issue(kind: str, chapter_number: int, message: str, previous: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_type": kind,
        "severity": "critical",
        "chapter_number": chapter_number,
        "description": message,
        "revision_required": True,
        "blocking": True,
        "opening_revision_brief": {
            "previous_ending_evidence": ((previous.get("ending_state") or {}).get("excerpt") if isinstance(previous.get("ending_state"), dict) else "") or "",
            "current_opening_problem": message,
            "required_bridge_type": "chapter_execution_draft",
        },
        "draft": draft,
    }


def _content_draft_issue(kind: str, chapter_number: int, message: str, draft: dict[str, Any], opening: str) -> dict[str, Any]:
    return {
        "issue_type": kind,
        "severity": "critical",
        "chapter_number": chapter_number,
        "description": message,
        "revision_required": True,
        "blocking": True,
        "opening_revision_brief": {
            "previous_ending_evidence": str(draft.get("opening_bridge") or "")[:500],
            "current_opening_problem": opening[:500],
            "required_bridge_type": "chapter_execution_draft",
        },
        "execution_draft": draft,
    }


def _constraint_issue(
    issue_type: str,
    constraint: dict[str, Any],
    chapter_number: int,
    message: str,
    opening: str,
) -> dict[str, Any]:
    severity = "critical" if constraint.get("severity") == "blocking" else "warning"
    issue = {
        "issue_type": issue_type,
        "severity": severity,
        "chapter_number": chapter_number,
        "description": message,
        "revision_required": severity == "critical",
        "blocking": severity == "critical",
        "constraint_type": constraint.get("constraint_type"),
        "constraint": constraint,
        "constraint_gate_status": "needs_review" if severity == "critical" else "passed",
        "evidence": [
            {
                "constraint_type": constraint.get("constraint_type"),
                "anchor": constraint.get("anchor"),
                "constraint_evidence": constraint.get("evidence") or [],
                "current_opening": opening[:240],
            }
        ],
        "opening_revision_brief": {
            "previous_ending_evidence": str(constraint.get("evidence") or "")[:500],
            "current_opening_problem": message,
            "required_bridge_type": str(constraint.get("constraint_type") or "continuity_constraint"),
            "continuity_constraints": [constraint],
            "rewrite_requirements": [
                "只替换本章开头100-300字，不重写后续正文。",
                "优先兑现 blocking 统一约束；如需改名、延期、换地点或移除人物，必须在正文中明示原因。",
                str(constraint.get("repair_hint") or ""),
            ],
            "preserve_after_opening": "保留当前章节后续有效内容，只替换或补写开头承接段。",
        },
    }
    return issue


def _review_entity_identity_constraint(constraint: dict[str, Any], opening: str, chapter_number: int) -> dict[str, Any]:
    evidence = constraint.get("evidence") if isinstance(constraint.get("evidence"), list) else []
    previous = next((item for item in evidence if isinstance(item, dict)), {})
    previous_name = str(previous.get("name") or constraint.get("anchor") or "").strip()
    if not previous_name or previous_name in opening:
        return {}
    current_profiles = _extract_entity_profiles(opening)
    previous_surname = previous_name[:1]
    for current in current_profiles:
        current_name = str(current.get("name") or "")
        if not current_name or current_name == previous_name:
            continue
        if current_name[:1] != previous_surname:
            continue
        previous_age = str(previous.get("age") or "")
        current_age = str(current.get("age") or "")
        previous_role = str(previous.get("role") or "")
        current_role = str(current.get("role") or "")
        if (previous_age and current_age and previous_age != current_age) or (previous_role and current_role and previous_role != current_role):
            return _constraint_issue(
                "evolution_entity_identity_drift",
                constraint,
                chapter_number,
                f"上一约束锁定人物为{previous_name}（{previous_age}岁，{previous_role}），本章开头出现{current_name}（{current_age}岁，{current_role}），疑似人物/实体身份漂移。",
                opening,
            )
    return {}


def _review_time_pressure_constraint(constraint: dict[str, Any], opening: str, chapter_number: int) -> dict[str, Any]:
    evidence = constraint.get("evidence") if isinstance(constraint.get("evidence"), list) else []
    previous = next((item for item in evidence if isinstance(item, dict)), {})
    previous_hours = int(previous.get("hours") or 0)
    if previous_hours <= 0:
        return {}
    current_deadlines = _extract_deadlines(opening)
    for current in current_deadlines:
        current_hours = int(current.get("hours") or 0)
        if current_hours > previous_hours and not any(token in opening for token in ("延期", "争取到", "重新计算", "误差", "代价", "宽限", "推迟")):
            return _constraint_issue(
                "evolution_time_pressure_drift",
                constraint,
                chapter_number,
                f"上一约束期限为{previous.get('text')}，本章开头写成{current.get('text')}，时间压力被静默放宽。",
                opening,
            )
    return {}


def _review_location_transition_constraint(constraint: dict[str, Any], opening: str, chapter_number: int) -> dict[str, Any]:
    anchor = str(constraint.get("anchor") or "").strip()
    if not anchor:
        return {}
    if anchor in opening or _has_route_bridge(opening):
        return {}
    opening_locations = _extract_locations(opening)
    if opening_locations or any(word in opening for word in ("来到", "抵达", "进入", "推开", "走进", "回到")):
        return _constraint_issue(
            "evolution_constraint_location_transition",
            constraint,
            chapter_number,
            f"统一约束要求从{anchor}承接，但本章开头未出现该地点，也缺少移动/耗时/撤离/视角桥接。",
            opening,
        )
    return {}


def _review_character_constraint(constraint: dict[str, Any], opening: str, chapter_number: int) -> dict[str, Any]:
    anchor = str(constraint.get("anchor") or "").strip()
    if not anchor or anchor in opening or _has_route_bridge(opening):
        return {}
    if constraint.get("severity") == "blocking":
        return _constraint_issue(
            "evolution_constraint_character_state",
            constraint,
            chapter_number,
            f"统一约束要求交代{anchor}的人物状态，但本章开头没有出现该人物或明确分离/离场桥接。",
            opening,
        )
    return {}


def _review_anchor_or_bridge_constraint(constraint: dict[str, Any], opening: str, chapter_number: int) -> dict[str, Any]:
    anchor = str(constraint.get("anchor") or "").strip()
    if not anchor:
        return {}
    terms = _keyword_terms(anchor)
    if anchor in opening or any(term in opening for term in terms[:4]) or _has_route_bridge(opening):
        return {}
    return _constraint_issue(
        "evolution_constraint_unfulfilled",
        constraint,
        chapter_number,
        f"统一约束未被兑现：{anchor}。本章开头缺少对应证据或桥接。",
        opening,
    )


def _summary_text(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("short_summary",):
        parts.append(str(summary.get(key) or ""))
    for key in ("opening_state", "ending_state", "chapter_state"):
        state = summary.get(key) if isinstance(summary.get(key), dict) else {}
        parts.append(str(state.get("excerpt") or ""))
    carry = summary.get("carry_forward") if isinstance(summary.get("carry_forward"), dict) else {}
    boundary = carry.get("boundary_state") if isinstance(carry.get("boundary_state"), dict) else {}
    parts.append(str(boundary.get("ending_excerpt") or ""))
    parts.append(str(boundary.get("immediate_threat") or ""))
    parts.append(str(boundary.get("active_goal") or ""))
    return _clean(" ".join(parts))


def _extract_entity_profiles(text: str) -> list[dict[str, Any]]:
    profiles = []
    for match in _PROFILE_RE.finditer(str(text or "")):
        name = str(match.group("name") or "").strip()
        role = str(match.group("role") or "").strip()
        if not name or role in {"一个学生", "那个学生"}:
            continue
        profiles.append({
            "name": name,
            "age": _cn_number_to_int(match.group("age")),
            "role": role,
            "text": match.group(0),
        })
    return _dedupe_profiles(profiles)[:8]


def _extract_deadlines(text: str) -> list[dict[str, Any]]:
    deadlines = []
    for match in _DEADLINE_RE.finditer(str(text or "")):
        raw = match.group(0).strip()
        prefix = str(match.group(1) or "")
        if not prefix and not any(token in raw for token in ("小时", "天", "日")):
            continue
        number = _cn_number_to_int(match.group("num"))
        unit = str(match.group("unit") or "")
        if number <= 0:
            continue
        hours = number * 24 if unit in {"天", "日"} else number
        if not prefix and hours < 24:
            continue
        deadlines.append({"text": raw, "hours": hours, "unit": unit})
    return deadlines[:6]


def _cn_number_to_int(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    special = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "二十四": 24, "四十八": 48, "七十二": 72}
    if text in special:
        return special[text]
    if "十" in text:
        left, _, right = text.partition("十")
        tens = special.get(left, 1) if left else 1
        ones = special.get(right, 0) if right else 0
        return tens * 10 + ones
    return 0


def _dedupe_profiles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = (item.get("name"), item.get("age"), item.get("role"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_constraints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = (item.get("constraint_type"), item.get("scope"), item.get("anchor"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _boundary_terms_handled_for_draft(text: str, expected: str) -> bool:
    compact_text = _clean(text)
    compact_expected = _clean(expected)
    if not compact_expected:
        return True
    if any(token in compact_expected and token in compact_text for token in THREAT_WORDS):
        return True
    if any(token and token in compact_text for token in _keyword_terms(compact_expected)):
        return True
    return _has_any_bridge(compact_text)


def _keyword_terms(text: str) -> list[str]:
    raw = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,8}", text)
    blocked = {"本章", "上一章", "结尾", "必须", "开头", "如果", "地点", "时间", "角色", "声音", "东西"}
    return [item for item in raw if item not in blocked][:8]


def _has_any_bridge(text: str) -> bool:
    return any(word in str(text or "") for word in BRIDGE_WORDS)


def _has_route_bridge(text: str) -> bool:
    return any(word in str(text or "") for word in ROUTE_BRIDGE_WORDS)


def _route_bridge_fulfilled(opening: str, route_bridge: str, required_terms: list[str]) -> bool:
    text = _clean(opening)
    if _has_route_bridge(text):
        return True
    strong_terms = [term for term in required_terms if len(str(term)) >= 2]
    if strong_terms and any(term in text for term in strong_terms[:8]):
        return True
    return _boundary_terms_handled_for_draft(text, route_bridge)


def _character_state_fulfilled(opening: str, character_positions: dict[str, Any]) -> bool:
    text = _clean(opening)
    entries = list(character_positions.items())[:4]
    if not entries:
        return True
    mentioned = 0
    stateful_required = False
    stateful_handled = False
    for name, state in entries:
        if str(name) and str(name) in text:
            mentioned += 1
        if isinstance(state, dict):
            state_text = str(state.get("state") or "")
            if any(word in state_text for word in STATE_WORDS):
                stateful_required = True
                if any(word in text for word in STATE_WORDS) or any(term in text for term in _keyword_terms(state_text)):
                    stateful_handled = True
    if mentioned:
        return True if not stateful_required else stateful_handled
    return _has_route_bridge(text)


def _render_object_states_for_draft(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        obj = str(item.get("object") or "").strip()
        snippet = _clean(item.get("snippet") or "")
        if obj and snippet:
            parts.append(f"{obj}:{snippet[:80]}")
    return "；".join(parts)


def _render_character_positions(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for name, state in list(value.items())[:6]:
        if isinstance(state, dict):
            location = str(state.get("location") or "").strip()
            status = str(state.get("state") or "").strip()
            text = " / ".join(item for item in (location, status) if item)
        else:
            text = str(state or "").strip()
        if name and text:
            parts.append(f"{name}:{text[:80]}")
    return "；".join(parts)


def _open_threads(sentences: list[str]) -> list[str]:
    signals = ("?", "？", "为什么", "怎么", "不知道", "还不知道", "没有回答", "谁", "什么", "伏笔", "警告", "等你")
    result = [sentence for sentence in sentences[-8:] if any(signal in sentence for signal in signals)]
    return [_clean(item)[:160] for item in result[-4:]]


def _sentences(content: str) -> list[str]:
    return [_clean(item) for item in _SENTENCE_SPLIT_RE.split(str(content or "")) if _clean(item)]


def _window(content: str, *, head: bool, limit: int = 520) -> str:
    text = str(content or "").strip()
    return text[:limit] if head else text[-limit:]


def _extract_locations(text: str) -> list[str]:
    found = [marker for marker in LOCATION_MARKERS if marker in text]
    generic = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_-]{0,8}(?:学院|档案库|宿舍|礼堂|机房|楼顶|塔顶|平台|电梯井|设备间|工坊|避难点|水箱)", text)
    generic = [_clean_location(item) for item in generic]
    generic = [item for item in generic if item and item not in found]
    return _prefer_specific_locations(_dedupe([*found, *generic]))[:10]


def _clean_location(value: str) -> str:
    text = str(value or "").strip()
    for prefix in ("但", "然后", "已经", "主楼", "根据", "发件人是", "大多穿着深灰色的"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    for marker in sorted(LOCATION_MARKERS, key=len, reverse=True):
        if text != marker and text.endswith(marker):
            return marker
    return text


def _prefer_specific_locations(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in sorted(items, key=len, reverse=True):
        if any(item != other and item in other for other in result):
            continue
        result.append(item)
    return sorted(result, key=lambda value: items.index(value))


def _extract_characters(text: str, source: str) -> list[str]:
    names = [name for name in DEFAULT_CHARACTERS if (name in text or name in source) and _is_character_mention(name, text, source)]
    return _dedupe(names)


def _is_character_mention(name: str, text: str, source: str) -> bool:
    if name not in AMBIGUOUS_CHARACTER_NAMES:
        return True
    snippets = _mention_snippets(name, f"{text}\n{source}")
    if not snippets:
        return False
    positive = AMBIGUOUS_CHARACTER_CONTEXT.get(name, ())
    negative = AMBIGUOUS_NON_CHARACTER_CONTEXT.get(name, ())
    for snippet in snippets:
        if any(word in snippet for word in positive):
            return True
    return not all(any(word in snippet for word in negative) for snippet in snippets)


def _mention_snippets(name: str, text: str) -> list[str]:
    snippets = []
    for match in re.finditer(re.escape(name), str(text or "")):
        start = max(0, match.start() - 24)
        end = min(len(text), match.end() + 36)
        snippets.append(text[start:end])
    return snippets[:8]


def _extract_object_states(text: str) -> list[dict[str, str]]:
    states = []
    sentences = _sentences(text)
    for obj in TRACKED_OBJECTS:
        for sentence in sentences:
            if obj in sentence:
                states.append({"object": obj, "snippet": sentence[:180]})
                break
    return states[:8]


def _extract_actions(text: str) -> list[str]:
    actions = []
    for sentence in _sentences(text):
        if any(word in sentence for word in (*ARRIVAL_WORDS, *LEAVE_WORDS, "解锁", "播放", "发热", "锁进", "取出")):
            actions.append(sentence[:140])
    return actions[:8]


def _has_arrival_reset(text: str, location: str) -> bool:
    if location not in text:
        return False
    if f"擅自进入{location}" in text:
        return False
    patterns = [
        f"才找到{location}",
        f"第一次找到{location}",
        f"终于找到{location}",
        f"进入{location}",
        f"走进{location}",
        f"来到{location}",
        f"抵达{location}",
        f"推开{location}",
    ]
    if any(pattern in text for pattern in patterns):
        return True
    for marker in (f"{location}门口", f"{location}的门", f"{location}门禁"):
        position = text.find(marker)
        if position >= 0 and any(token in text[position : position + 80] for token in ("刷卡", "进门", "推开", "没有弹开", "禁止进入")):
            return True
    return False


def _is_object_stateful(text: str) -> bool:
    return any(
        token in text
        for token in ("锁进", "放在", "取出", "塞进", "收进", "掏出", "交给", "递给", "插上", "播放", "解锁", "发热", "掉在")
    )


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _as_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


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


def _last(value: Any) -> str:
    items = _as_strings(value)
    return items[-1] if items else ""
