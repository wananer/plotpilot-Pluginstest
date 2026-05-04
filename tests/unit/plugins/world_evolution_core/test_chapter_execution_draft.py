from plugins.world_evolution_core.continuity import (
    build_chapter_execution_draft,
    build_chapter_summary,
    build_continuity_constraint_state,
    build_continuity_constraints_from_summary,
    _constraint_issue,
    render_chapter_execution_draft,
    repair_chapter_execution_draft,
    review_chapter_execution_draft,
    review_continuity_constraints_against_content,
    review_execution_draft_against_content,
)
from plugins.world_evolution_core.review_rules import review_boundary_state


def test_previous_boundary_enters_chapter_execution_draft():
    previous = build_chapter_summary(
        "novel-draft",
        1,
        "沈砚和林照站在石室门口。雨声里，石室深处传来窸窣声，像有什么东西正在朝门口爬行。",
        "now",
    )

    draft = build_chapter_execution_draft(
        "novel-draft",
        2,
        "沈砚继续调查石室深处的声音。",
        previous,
    )

    assert draft["start_location"] == "石室门口"
    assert "沈砚" in draft["onscreen_characters"]
    assert "林照" in draft["onscreen_characters"]
    assert "窸窣" in draft["immediate_threat"]
    assert "石室门口" in render_chapter_execution_draft(draft)


def test_execution_draft_repair_prefers_boundary_over_outline_jump():
    previous = build_chapter_summary(
        "novel-draft",
        1,
        "沈砚和林照被困在地下石室，石板后传来沙哑声音：明天午夜他会醒。",
        "now",
    )
    draft = build_chapter_execution_draft(
        "novel-draft",
        2,
        "清晨，沈砚来到戒律堂寻找新线索。",
        previous,
    )
    draft["start_location"] = "戒律堂"
    draft["opening_bridge"] = "直接开始新调查。"

    issues = review_chapter_execution_draft(previous, draft, 2)
    repaired = repair_chapter_execution_draft(draft, previous)

    assert issues
    assert repaired["start_location"] == "地下石室"
    assert review_chapter_execution_draft(previous, repaired, 2) == []


def test_content_must_fulfill_execution_draft_opening_contract():
    draft = {
        "start_location": "地下石室",
        "onscreen_characters": ["沈砚", "林照"],
        "immediate_threat": "石板后传来沙哑声音：明天午夜他会醒。",
        "opening_bridge": "从地下石室原地续接，先处理石板后的声音。",
    }

    bad = "清晨，沈砚独自来到戒律堂，翻开新的卷宗。"
    good = "地下石室里，沈砚和林照仍贴着石板站着。那道沙哑声音消失后，黑暗反而更沉。"

    assert review_execution_draft_against_content(draft, bad, 2)
    assert review_execution_draft_against_content(draft, good, 2) == []


def test_route_state_enters_summary_and_execution_draft():
    previous = build_chapter_summary(
        "novel-route",
        1,
        "许衡从档案馆侧门逃入地下大厅，李雯受伤流血，影子还在追踪他们。城市记忆存储站03号节点的铁门在身后合上。",
        "now",
    )

    carry = previous["carry_forward"]
    draft = build_chapter_execution_draft(
        "novel-route",
        2,
        "许衡继续寻找许念留下的信息。",
        previous,
    )

    assert carry["route_state"]["end_location"] == "城市记忆存储站03号节点"
    assert carry["character_positions"]["李雯"]["state"]
    assert "opening_route_bridge" in draft
    assert "character_positions" in draft
    assert "required_evidence_terms" in draft
    rendered = render_chapter_execution_draft(draft)
    assert "路线桥接" in rendered
    assert "人物位置状态" in rendered


def test_ambiguous_shadow_noun_is_not_extracted_as_character():
    previous = build_chapter_summary(
        "novel-shadow-filter",
        1,
        "实验室的门在身后关上。走廊里的应急灯闪烁着惨白的光，在地面上投下一道道斑驳的影子。电梯门打开，冷风扑面而来。",
        "now",
    )

    carry = previous["carry_forward"]
    draft = build_chapter_execution_draft(
        "novel-shadow-filter",
        2,
        "周砚继续追查手机上的坐标。",
        previous,
    )

    assert "影子" not in carry["onscreen_characters"]
    assert "影子" not in carry["character_positions"]
    assert "影子" not in draft["onscreen_characters"]
    assert all(item.get("anchor") != "影子" for item in draft["continuity_constraints"])


def test_content_must_fulfill_route_and_character_state():
    draft = {
        "start_location": "城市记忆存储站03号节点",
        "onscreen_characters": ["许衡", "李雯"],
        "opening_bridge": "从城市记忆存储站03号节点原地续接。",
        "opening_route_bridge": "从上一章终点城市记忆存储站03号节点承接，先写清被追踪后的即时位置。",
        "character_positions": {
            "许衡": {"location": "城市记忆存储站03号节点", "state": "被追踪"},
            "李雯": {"location": "城市记忆存储站03号节点", "state": "受伤、流血"},
        },
        "required_evidence_terms": ["城市记忆存储站03号节点", "追踪", "李雯", "受伤"],
    }

    bad = "清晨，许衡推开档案馆侧门，准备重新调查父亲留下的办公室。"
    good = "城市记忆存储站03号节点里，许衡扶着仍在流血的李雯贴墙前行。影子的追踪光束扫过服务器机柜，他只能沿着维修通道继续撤离。"

    issues = review_execution_draft_against_content(draft, bad, 2)
    assert any(issue["issue_type"] == "chapter_route_bridge_unfulfilled" for issue in issues)
    assert review_execution_draft_against_content(draft, good, 2) == []


def test_boundary_gate_flags_location_jump_without_route_bridge():
    previous = build_chapter_summary(
        "novel-route",
        9,
        "许衡坠入地下大厅。墙上的标识牌写着城市记忆存储站03号节点，影子的手电光从井口扫下来。",
        "now",
    )
    opening = "许衡推开档案馆侧门，走进父亲办公室，准备翻找新的线索。"

    issues = review_boundary_state([previous], opening, 10)

    assert any(issue["issue_type"] == "evolution_route_missing_transition" for issue in issues)


def test_unified_constraints_cover_route_character_object_and_deadline():
    previous = build_chapter_summary(
        "novel-constraints",
        3,
        "许衡扶着受伤流血的李雯撤到城市记忆存储站03号节点。许念的倒计时最多四十八小时，黑匣子仍在许衡背包里发热。",
        "now",
    )

    constraints = build_continuity_constraints_from_summary(previous)
    types = {item["constraint_type"] for item in constraints}
    draft = build_chapter_execution_draft("novel-constraints", 4, "许衡继续寻找许念。", previous)
    rendered = render_chapter_execution_draft(draft)

    assert "location_transition" in types
    assert "character_state" in types
    assert "object_state" in types
    assert "time_pressure" in types
    assert draft["continuity_constraints"]
    assert "统一连续性约束" in rendered


def test_unified_entity_identity_drift_triggers_blocking_issue():
    previous = build_chapter_summary(
        "novel-identity",
        8,
        "屏幕亮起，资料写着：陈晓雨，二十二岁，艺术学院大三学生。许衡意识到她就是失踪名单里唯一留下脑波记录的人。",
        "now",
    )
    opening = "清晨，陈雨薇，二十三岁，神经科学系研究生，推开中枢大楼的玻璃门。"

    issues = review_continuity_constraints_against_content(previous, opening, 9)

    assert any(issue["issue_type"] == "evolution_entity_identity_drift" for issue in issues)
    issue = next(issue for issue in issues if issue["issue_type"] == "evolution_entity_identity_drift")
    assert issue["constraint_type"] == "entity_identity"
    assert issue["blocking"] is True


def test_unified_time_pressure_drift_triggers_issue():
    previous = build_chapter_summary(
        "novel-deadline",
        3,
        "许念的脑波备份最多四十八小时就会被中枢覆盖，许衡必须在期限前找到她。",
        "now",
    )
    opening = "许衡看着表，知道他们还有三天时间慢慢排查中枢大楼。"

    issues = review_continuity_constraints_against_content(previous, opening, 6)

    assert any(issue["issue_type"] == "evolution_time_pressure_drift" for issue in issues)


def test_unified_constraint_issue_shape_is_generic():
    constraint = build_continuity_constraint_state(
        "object_state",
        "next_chapter_opening",
        "黑匣子",
        [{"snippet": "黑匣子仍在许衡背包里发热"}],
        "blocking",
        "开头必须交代黑匣子由谁持有。",
        0.9,
    )

    issues = review_continuity_constraints_against_content(
        {"carry_forward": {"continuity_constraints": [constraint]}},
        "清晨，许衡走进档案馆侧门。",
        4,
    )

    assert issues[0]["constraint_type"] == "object_state"
    assert issues[0]["constraint"]["anchor"] == "黑匣子"
    assert issues[0]["opening_revision_brief"]["continuity_constraints"][0]["constraint_type"] == "object_state"


def test_unified_constraint_warning_issue_keeps_gate_status_allowed():
    constraint = build_continuity_constraint_state(
        "location_transition",
        "next_chapter_opening",
        "档案馆侧门",
        [{"from": "地下大厅", "to": "档案馆侧门"}],
        "warning",
        "开头应补出从地下大厅返回档案馆侧门的路线证据。",
        0.82,
    )

    issue = _constraint_issue(
        "evolution_route_missing_transition",
        constraint,
        10,
        "路线桥接不足",
        "许衡推开档案馆侧门，走进父亲办公室，准备翻找新的线索。",
    )

    assert issue["severity"] == "warning"
    assert issue["constraint_gate_status"] in {"passed", "auto_revised", "needs_review", "skipped"}
    assert issue["constraint_gate_status"] != "warning"
    for key in ("constraint_type", "severity", "evidence", "opening_revision_brief"):
        assert key in issue
