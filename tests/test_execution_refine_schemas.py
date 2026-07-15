from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    ExecutionRefineApplyRequest,
    ExecutionRefineProposal,
    ExecutionRefineRequest,
    SseEventEnvelope,
)


def _proposal_payload(**overrides):
    task_id = uuid4()
    payload = {
        "schema_version": 1,
        "proposal_type": "execution_refine",
        "mode": "progress_recovery",
        "summary": "缩小当前执行范围",
        "user_facing_reasons": ["先恢复最关键的行动"],
        "preserved_constraints": ["已完成工作保持不变"],
        "operations": [
            {
                "operation_type": "update_task",
                "task_id": str(task_id),
                "changes": {"estimated_minutes": 20},
                "reason": "缩短当前任务",
            }
        ],
        "focus_task_ids": [],
        "estimated_focus_minutes": 0,
        "buffer_minutes": 0,
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def test_execution_refine_request_accepts_each_supported_mode():
    assert ExecutionRefineRequest(
        request_id=uuid4(),
        mode="time_budget",
        available_minutes=20,
    ).available_minutes == 20
    assert ExecutionRefineRequest(
        request_id=uuid4(),
        mode="progress_recovery",
    ).mode == "progress_recovery"
    assert ExecutionRefineRequest(
        request_id=uuid4(),
        mode="context_change",
        user_context="演示优先于报告",
    ).mode == "context_change"


@pytest.mark.parametrize(
    "payload",
    [
        {"request_id": uuid4(), "mode": "time_budget"},
        {
            "request_id": uuid4(),
            "mode": "progress_recovery",
            "available_minutes": 20,
        },
        {"request_id": uuid4(), "mode": "context_change"},
        {"request_id": uuid4(), "mode": "unsupported"},
    ],
)
def test_execution_refine_request_rejects_invalid_mode_combinations(payload):
    with pytest.raises(ValidationError):
        ExecutionRefineRequest.model_validate(payload)


def test_execution_refine_request_rejects_duplicate_or_conflicting_references():
    task_id = uuid4()
    with pytest.raises(ValidationError):
        ExecutionRefineRequest(
            request_id=uuid4(),
            mode="context_change",
            priority_task_ids=[task_id, task_id],
        )
    with pytest.raises(ValidationError):
        ExecutionRefineRequest(
            request_id=uuid4(),
            mode="context_change",
            priority_task_ids=[task_id],
            blocked_task_ids=[task_id],
        )


def test_execution_refine_request_requires_timezone_aware_deadline():
    with pytest.raises(ValidationError):
        ExecutionRefineRequest(
            request_id=uuid4(),
            mode="context_change",
            new_deadline=datetime(2026, 7, 20, 12, 0),
        )
    parsed = ExecutionRefineRequest(
        request_id=uuid4(),
        mode="context_change",
        new_deadline=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
    )
    assert parsed.new_deadline.tzinfo is not None


def test_execution_refine_proposal_parses_all_four_discriminated_operations():
    first, second, parent = uuid4(), uuid4(), uuid4()
    proposal = ExecutionRefineProposal.model_validate(
        _proposal_payload(
            operations=[
                {
                    "operation_type": "update_task",
                    "task_id": str(first),
                    "changes": {"description": None, "estimated_minutes": 15},
                    "reason": "缩小范围",
                },
                {
                    "operation_type": "add_task",
                    "draft_id": "recovery_1",
                    "parent_task_id": str(parent),
                    "title": "列出三条恢复动作",
                    "description": None,
                    "estimated_minutes": 10,
                    "done_criteria": "写出三条动作",
                    "start_hint": "打开当前计划",
                    "fallback_action": "先写第一条",
                    "depends_on_refs": [str(first)],
                    "insert_after_task_id": str(first),
                    "reason": "补齐恢复步骤",
                },
                {
                    "operation_type": "reorder_siblings",
                    "parent_task_id": str(parent),
                    "ordered_task_ids": [str(second), str(first)],
                    "reason": "先做关键工作",
                },
                {
                    "operation_type": "set_my_day",
                    "task_id": str(first),
                    "is_in_my_day": True,
                    "reason": "加入即时执行",
                },
            ]
        )
    )
    assert [item.operation_type for item in proposal.operations] == [
        "update_task",
        "add_task",
        "reorder_siblings",
        "set_my_day",
    ]


@pytest.mark.parametrize(
    "operation",
    [
        {
            "operation_type": "delete_task",
            "task_id": str(uuid4()),
            "reason": "删除",
        },
        {
            "operation_type": "update_task",
            "task_id": str(uuid4()),
            "changes": {"status": "completed"},
            "reason": "越权修改",
        },
        {
            "operation_type": "update_task",
            "task_id": str(uuid4()),
            "changes": {"phase_id": "phase-2"},
            "reason": "越权修改",
        },
        {
            "operation_type": "update_task",
            "task_id": str(uuid4()),
            "changes": {},
            "reason": "空更新",
        },
    ],
)
def test_execution_refine_operations_reject_arbitrary_patches(operation):
    with pytest.raises(ValidationError):
        ExecutionRefineProposal.model_validate(
            _proposal_payload(operations=[operation])
        )


def test_execution_refine_contract_enforces_counts_and_unknown_fields():
    operations = _proposal_payload()["operations"] * 13
    with pytest.raises(ValidationError):
        ExecutionRefineProposal.model_validate(
            _proposal_payload(operations=operations)
        )
    with pytest.raises(ValidationError):
        ExecutionRefineRequest.model_validate(
            {
                "request_id": str(uuid4()),
                "mode": "progress_recovery",
                "arbitrary_patch": True,
            }
        )


def test_execution_refine_apply_request_accepts_only_optional_sha256_fingerprint():
    assert ExecutionRefineApplyRequest().expected_scope_fingerprint is None
    assert (
        ExecutionRefineApplyRequest(expected_scope_fingerprint="a" * 64)
        .expected_scope_fingerprint
        == "a" * 64
    )
    with pytest.raises(ValidationError):
        ExecutionRefineApplyRequest(expected_scope_fingerprint="not-a-fingerprint")
    with pytest.raises(ValidationError):
        ExecutionRefineApplyRequest.model_validate(
            {"selected_operation_ids": ["operation-1"]}
        )


def test_sse_contract_includes_execution_refine_run_and_events():
    envelope = SseEventEnvelope(
        event_id="event-1",
        thread_id="thread-1",
        request_id=str(uuid4()),
        run_type="execution_refine",
        event_type="diff_ready",
        seq=1,
        created_at=datetime.now(timezone.utc),
        payload={},
    )
    assert envelope.run_type == "execution_refine"
    assert envelope.event_type == "diff_ready"
