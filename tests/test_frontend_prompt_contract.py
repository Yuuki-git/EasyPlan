from pathlib import Path


def test_next_phase_prompt_preserves_original_intent_type():
    source = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "store"
        / "useAppStore.ts"
    ).read_text(encoding="utf-8")

    assert "请保持原始意图类型，不要重新解释为短期交付或情境清单；本次只是为同一目标解锁下一阶段。" in source
