"""Personality palette helpers for Evolution character cards."""
from __future__ import annotations

from typing import Any

DEFAULT_PALETTE_METAPHOR = "人的性格像调色盘：底色、主色调与点缀共同驱动行为。"
NATIVE_DERIVED_SOURCE = "native_bible_derived"
STRUCTURED_SOURCE = "structured_extraction"

_GENERIC_MENTAL_STATES = {"", "NORMAL", "PRESSURE_LOCKED", "UNKNOWN", "未定", "待观察", "默认"}
_SOURCE_PRIORITY = {
    "": 0,
    "default": 0,
    NATIVE_DERIVED_SOURCE: 20,
    "cast_derived": 20,
    "agent_derived": 40,
    STRUCTURED_SOURCE: 80,
    "manual": 100,
}


def derive_palette_from_native_character(
    *,
    name: str,
    description: Any = "",
    mental_state: Any = "",
    verbal_tic: Any = "",
    idle_behavior: Any = "",
) -> dict[str, Any]:
    """Derive a conservative non-empty palette from read-only native character fields."""
    clean_name = _clean(name, limit=40)
    fields = {
        "description": _clean(description, limit=400),
        "mental_state": _clean(mental_state, limit=120),
        "verbal_tic": _clean(verbal_tic, limit=120),
        "idle_behavior": _clean(idle_behavior, limit=120),
    }
    text = " ".join(value for value in fields.values() if value)
    if not text:
        return {}

    base = _derive_base(text, fields["mental_state"])
    tones = _derive_main_tones(text)
    accents = _derive_accents(fields)
    derivatives = _derive_derivatives(clean_name, base, tones, fields)
    source_refs = [
        {"source_type": "bible_character", "field": key, "character": clean_name}
        for key, value in fields.items()
        if value
    ]
    return {
        "metaphor": DEFAULT_PALETTE_METAPHOR,
        "base": base,
        "main_tones": tones,
        "accents": accents,
        "derivatives": derivatives,
        "source": NATIVE_DERIVED_SOURCE,
        "source_refs": source_refs[:6],
    }


def merge_palette_missing_fields(existing: Any, incoming: Any) -> dict[str, Any]:
    """Merge palette fields while preserving richer/manual palettes over derived fallbacks."""
    current = _normalize_palette(existing)
    candidate = _normalize_palette(incoming)
    if not _palette_has_content(candidate):
        return current

    current_priority = _priority(current, incoming=False)
    candidate_priority = _priority(candidate, incoming=True)
    may_replace = candidate_priority > current_priority and str(current.get("source") or "") == NATIVE_DERIVED_SOURCE

    changed = False
    if candidate.get("metaphor") and (not current.get("metaphor") or current.get("metaphor") == DEFAULT_PALETTE_METAPHOR):
        current["metaphor"] = candidate["metaphor"]
        changed = True
    for key in ("base",):
        if candidate.get(key) and (not current.get(key) or may_replace):
            current[key] = candidate[key]
            changed = True
    for key, limit in (("main_tones", 8), ("accents", 10), ("derivatives", 32)):
        incoming_items = candidate.get(key) if isinstance(candidate.get(key), list) else []
        current_items = current.get(key) if isinstance(current.get(key), list) else []
        if incoming_items and (not current_items or may_replace):
            current[key] = incoming_items[:limit]
            changed = True

    if changed:
        source = str(candidate.get("source") or "").strip()
        if source and (not current.get("source") or may_replace):
            current["source"] = source
        refs = _merge_source_refs(current.get("source_refs"), candidate.get("source_refs"))
        if refs:
            current["source_refs"] = refs
    return current


def palette_missing_fields(palette: Any) -> list[str]:
    data = palette if isinstance(palette, dict) else {}
    missing: list[str] = []
    if not _clean(data.get("base")):
        missing.append("base")
    if not data.get("main_tones"):
        missing.append("main_tones")
    if not data.get("derivatives"):
        missing.append("derivatives")
    return missing


def personality_palette_status(cards: list[dict[str, Any]]) -> dict[str, Any]:
    active = [card for card in cards if not _invalid_card(card)]
    missing_cards = []
    source_counts: dict[str, int] = {}
    for card in active:
        palette = card.get("personality_palette") if isinstance(card.get("personality_palette"), dict) else {}
        source = str(palette.get("source") or "unspecified")
        source_counts[source] = source_counts.get(source, 0) + 1
        missing = palette_missing_fields(palette)
        if missing:
            missing_cards.append(
                {
                    "name": str(card.get("name") or ""),
                    "last_seen_chapter": card.get("last_seen_chapter"),
                    "missing_fields": missing,
                    "source": source,
                }
            )
    complete = len(active) - len(missing_cards)
    return {
        "character_count": len(active),
        "complete_count": complete,
        "missing_count": len(missing_cards),
        "coverage": round(complete / len(active), 4) if active else 0.0,
        "source_counts": dict(sorted(source_counts.items())),
        "missing": missing_cards[:12],
    }


def _derive_base(text: str, mental_state: str) -> str:
    if any(term in text for term in ("姐姐", "坠塔", "旧案", "真相", "追查", "伦理审查")):
        return "执念求真"
    if any(term in text for term in ("黑客", "改装", "权限", "物联网", "档案室")):
        return "技术破局"
    if any(term in text for term in ("监察", "安保", "规程", "制服", "徽章")):
        return "秩序守护"
    if any(term in text for term in ("证据", "分析", "调查", "确认", "查清", "检查", "记录")):
        return "证据驱动"
    if any(term in text for term in ("警惕", "谨慎", "风险", "秘密", "别交", "防御")):
        return "谨慎防御"
    if mental_state and mental_state not in _GENERIC_MENTAL_STATES:
        return mental_state[:40]
    return "待观察的行动底色"


def _derive_main_tones(text: str) -> list[str]:
    rules = [
        (("证据", "分析", "确认", "查清", "调查", "检查"), "求证"),
        (("警惕", "谨慎", "风险", "秘密"), "谨慎"),
        (("姐姐", "坠塔", "旧案", "真相", "追查"), "追索"),
        (("黑客", "权限", "改装", "技术", "物联网"), "灵活破局"),
        (("规程", "监察", "安保", "制服"), "守序"),
        (("保护", "别交", "提醒", "警告"), "保护性防御"),
        (("记录", "读数", "档案"), "细节敏感"),
    ]
    tones: list[str] = []
    for keywords, tone in rules:
        if any(keyword in text for keyword in keywords):
            tones.append(tone)
    if not tones:
        tones = ["观察", "试探"]
    return _dedupe_strings(tones, limit=3)


def _derive_accents(fields: dict[str, str]) -> list[str]:
    accents: list[str] = []
    if fields.get("verbal_tic"):
        accents.append(f"口头锚点：{fields['verbal_tic'][:24]}")
    if fields.get("idle_behavior"):
        accents.append(f"待机动作：{fields['idle_behavior'][:24]}")
    if fields.get("mental_state") and fields["mental_state"] not in _GENERIC_MENTAL_STATES:
        accents.append(f"当前压力：{fields['mental_state'][:24]}")
    return _dedupe_strings(accents, limit=3)


def _derive_derivatives(name: str, base: str, tones: list[str], fields: dict[str, str]) -> list[dict[str, Any]]:
    primary = tones[0] if tones else base
    derivatives = [
        {
            "tone": primary,
            "title": "压力下的默认行动",
            "description": f"{name or '角色'}遇到风险或线索时，先沿着“{base}”底色行动，用{primary}回应，而不是突然切换成无来由的反应。",
            "trigger": "线索、风险或关系压力出现时",
            "visibility": "通过动作选择、说话方式和是否求证体现",
            "future": False,
        }
    ]
    if fields.get("verbal_tic"):
        derivatives.append(
            {
                "tone": "声线锚点",
                "title": "说话方式外显",
                "description": f"对话可围绕“{fields['verbal_tic'][:40]}”形成稳定声线，但不要机械复读。",
                "trigger": "需要表态、质疑或阻止他人时",
                "visibility": "短句、反问或确认式表达",
                "future": False,
            }
        )
    if fields.get("idle_behavior"):
        derivatives.append(
            {
                "tone": "动作锚点",
                "title": "无台词时的行为底纹",
                "description": f"沉默或等待时可用“{fields['idle_behavior'][:40]}”一类动作承接性格，而不是空站场。",
                "trigger": "等待、犹豫、观察环境时",
                "visibility": "手部动作、视线、位置选择",
                "future": False,
            }
        )
    return derivatives[:3]


def _normalize_palette(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "metaphor": DEFAULT_PALETTE_METAPHOR,
            "base": "",
            "main_tones": [],
            "accents": [],
            "derivatives": [],
        }
    data = dict(value)
    data["metaphor"] = _clean(data.get("metaphor"), limit=240) or DEFAULT_PALETTE_METAPHOR
    data["base"] = _clean(data.get("base"), limit=40)
    data["main_tones"] = _dedupe_strings(data.get("main_tones") or [], limit=8)
    data["accents"] = _dedupe_strings(data.get("accents") or [], limit=10)
    data["derivatives"] = _normalize_derivatives(data.get("derivatives"))
    if data.get("source"):
        data["source"] = _clean(data.get("source"), limit=60)
    if isinstance(data.get("source_refs"), list):
        data["source_refs"] = _merge_source_refs([], data.get("source_refs"))
    return data


def _normalize_derivatives(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if isinstance(item, str):
            record = {"tone": "", "title": "", "description": _clean(item, limit=300), "trigger": "", "visibility": "", "future": False}
        elif isinstance(item, dict):
            record = {
                "tone": _clean(item.get("tone"), limit=40),
                "title": _clean(item.get("title"), limit=60),
                "description": _clean(item.get("description"), limit=300),
                "trigger": _clean(item.get("trigger"), limit=120),
                "visibility": _clean(item.get("visibility"), limit=120),
                "future": bool(item.get("future")),
            }
        else:
            continue
        if not record["description"]:
            continue
        key = (record["tone"], record["title"], record["description"])
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result[:32]


def _priority(palette: dict[str, Any], *, incoming: bool) -> int:
    source = str(palette.get("source") or "").strip()
    if source:
        return _SOURCE_PRIORITY.get(source, 60)
    if _palette_has_content(palette):
        return 80 if incoming else 100
    return 0


def _palette_has_content(palette: dict[str, Any]) -> bool:
    return bool(_clean(palette.get("base")) or palette.get("main_tones") or palette.get("accents") or palette.get("derivatives"))


def _merge_source_refs(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in [*(existing if isinstance(existing, list) else []), *(incoming if isinstance(incoming, list) else [])]:
        if not isinstance(item, dict):
            continue
        record = {
            "source_type": _clean(item.get("source_type"), limit=60),
            "field": _clean(item.get("field"), limit=60),
            "character": _clean(item.get("character"), limit=60),
        }
        if not any(record.values()):
            continue
        key = (record["source_type"], record["field"], record["character"])
        if key in seen:
            continue
        seen.add(key)
        refs.append(record)
    return refs[:8]


def _invalid_card(card: dict[str, Any]) -> bool:
    return str(card.get("status") or "") == "invalid_entity" or str(card.get("entity_type") or "") == "non_person"


def _dedupe_strings(items: Any, *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return result
    for item in items:
        value = _clean(item, limit=160)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result[:limit]


def _clean(value: Any, *, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]
