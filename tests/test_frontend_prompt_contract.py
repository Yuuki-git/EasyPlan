from pathlib import Path


def test_next_phase_generation_uses_same_thread_phase_endpoint_not_new_intent():
    source = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "store"
        / "useAppStore.ts"
    ).read_text(encoding="utf-8")
    function_start = source.index("generateNextPhasePlan: async () =>")
    function_end = source.index("submitIntent: async", function_start)
    function_source = source[function_start:function_end]

    assert "/api/threads/${selectedProjectId}/phases/next" in function_source
    assert "fetch('/api/intents'" not in function_source
