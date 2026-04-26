import json

from scripts.evaluation.evolution_pressure_test import (
    _build_api2_control_card_prompt,
    _load_existing_arm,
    _repetitive_phrase_counts,
    _repetitive_phrase_total,
)


def test_repetitive_phrase_metrics_catch_silent_templates():
    content = "沈砚没有说话。顾岚没有回答。两人沉默了几秒，然后继续沉默。"

    counts = _repetitive_phrase_counts(content)

    assert counts["没有说话"] == 1
    assert counts["没有回答"] == 1
    assert counts["沉默了几秒"] == 1
    assert counts["沉默"] == 2
    assert _repetitive_phrase_total(content) == 4


def test_api2_control_card_prompt_is_compression_not_generation():
    prompt = _build_api2_control_card_prompt(
        chapter_number=7,
        outline="三人潜入潮汐机房，黑匣子投影出争执。",
        raw_evolution_context="上一章结尾：沈砚已经进入C307，黑匣子仍在他手里。",
    )

    assert "Evolution 状态压缩器" in prompt
    assert "不写正文" in prompt
    assert "只输出控制卡" in prompt
    assert "沈砚已经进入C307" in prompt
    assert "没有说话/没有回答" in prompt


def test_load_existing_arm_preserves_reused_usage_metadata(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    output_dir.mkdir()
    for index in range(1, 11):
        (source_dir / f"control_off_chapter_{index:02d}.md").write_text(
            f"第{index}章正文。沈砚继续调查。",
            encoding="utf-8",
        )
    (source_dir / "llm_usage.json").write_text(
        json.dumps(
            {
                "generation": {
                    "control_off": {
                        "aggregate": {"call_count": 1, "total_tokens": 300, "total_cost_usd": 0.02},
                        "calls": [
                            {
                                "call_count": 1,
                                "chapter_number": 1,
                                "phase": "chapter_generation",
                                "input_tokens": 100,
                                "output_tokens": 200,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0,
                                "non_cache_tokens": 300,
                                "total_tokens": 300,
                                "total_cost_usd": 0.02,
                                "duration_seconds": 12.5,
                                "usage_source": "claude_json_usage",
                                "model": "sonnet",
                            }
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (source_dir / "metrics.json").write_text(
        json.dumps(
            {
                "control_off": {
                    "chapters": [
                        {
                            "chapter_number": 1,
                            "prompt_chars": 1234,
                            "evolution_context_chars": 0,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chapters, meta = _load_existing_arm(source_dir, output_dir, "control_off")

    assert meta["llm_usage"]["call_count"] == 1
    assert meta["llm_calls"][0]["phase"] == "chapter_generation"
    assert chapters[0].prompt_chars == 1234
    assert chapters[0].llm_call_count == 1
    assert chapters[0].llm_total_tokens == 300
    assert chapters[0].llm_total_cost_usd == 0.02
    assert chapters[0].duration_seconds == 12.5
    assert (output_dir / "control_off_chapter_01.md").exists()
