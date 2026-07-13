from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.schemas import (
    DecomposeAssistProposal,
    StartAssistProposal,
    TaskAssistApplyRequest,
    TaskAssistProposal,
    TaskAssistRequest,
    UnstickAssistProposal,
)


def _draft(**overrides):
    payload = {
        "draft_id": "draft-1",
        "title": "列出市场分析的三个证据来源",
        "description": None,
        "estimated_minutes": 5,
        "done_criteria": "写下三个可核验的来源名称",
        "start_hint": "打开现有调研笔记",
        "fallback_action": None,
    }
    payload.update(overrides)
    return payload


def test_task_assist_request_enforces_mode_and_context_limit():
    request = TaskAssistRequest(request_id=uuid4(), mode="start", user_context="卡在第一步")
    assert request.mode == "start"

    with pytest.raises(ValidationError):
        TaskAssistRequest(request_id=uuid4(), mode="explain", user_context=None)
    with pytest.raises(ValidationError):
        TaskAssistRequest(request_id=uuid4(), mode="start", user_context="x" * 1001)


def test_start_proposal_enforces_two_to_ten_minute_starter():
    proposal = StartAssistProposal(
        proposal_type="start",
        summary="先完成一个可见的小步骤",
        starter_step=_draft(estimated_minutes=10),
    )
    assert proposal.starter_step.estimated_minutes == 10

    with pytest.raises(ValidationError):
        StartAssistProposal(
            proposal_type="start",
            summary="太长",
            starter_step=_draft(estimated_minutes=11),
        )


def test_unstick_proposal_requires_two_or_three_unique_options_and_valid_recommendation():
    proposal = UnstickAssistProposal(
        proposal_type="unstick",
        obstacle_summary="缺少可核验资料",
        recommended_option_id="option-1",
        options=[
            {
                "option_id": "option-1",
                "title": "先找内部材料",
                "action": "搜索项目目录中的调研记录",
                "estimated_minutes": 10,
                "tradeoff": "速度快，但信息可能不完整",
            },
            {
                "option_id": "option-2",
                "title": "先列缺口",
                "action": "列出当前缺少的三个数据点",
                "estimated_minutes": 5,
                "tradeoff": "不会立即补齐材料，但能缩小范围",
            },
        ],
    )
    assert len(proposal.options) == 2

    with pytest.raises(ValidationError):
        UnstickAssistProposal(
            proposal_type="unstick",
            obstacle_summary="缺少资料",
            recommended_option_id="missing",
            options=proposal.options,
        )


def test_decompose_proposal_enforces_two_to_five_subtasks():
    proposal = DecomposeAssistProposal(
        proposal_type="decompose",
        summary="按证据、判断和结论拆分",
        completion_rule="all_subtasks_completed",
        subtasks=[_draft(), _draft(draft_id="draft-2", title="写出市场判断")],
        dependencies=[
            {"task_draft_id": "draft-2", "depends_on_draft_id": "draft-1"}
        ],
    )
    assert len(proposal.subtasks) == 2

    with pytest.raises(ValidationError):
        DecomposeAssistProposal(
            proposal_type="decompose",
            summary="过少",
            completion_rule="all_subtasks_completed",
            subtasks=[_draft()],
        )


@pytest.mark.parametrize("proposal_type", ["start", "unstick", "decompose"])
def test_task_assist_proposal_is_discriminated_by_proposal_type(proposal_type):
    payloads = {
        "start": {
            "proposal_type": "start",
            "summary": "先做一步",
            "starter_step": _draft(),
        },
        "unstick": {
            "proposal_type": "unstick",
            "obstacle_summary": "范围不清",
            "recommended_option_id": "a",
            "options": [
                {
                    "option_id": "a",
                    "title": "缩小范围",
                    "action": "圈定一个小节",
                    "estimated_minutes": 5,
                    "tradeoff": "先牺牲完整度",
                },
                {
                    "option_id": "b",
                    "title": "补一条证据",
                    "action": "查找一条来源",
                    "estimated_minutes": 10,
                    "tradeoff": "需要额外检索",
                },
            ],
        },
        "decompose": {
            "proposal_type": "decompose",
            "summary": "拆成两步",
            "completion_rule": "all_subtasks_completed",
            "subtasks": [_draft(), _draft(draft_id="draft-2")],
            "dependencies": [],
        },
    }
    proposal = TypeAdapter(TaskAssistProposal).validate_python(payloads[proposal_type])
    assert proposal.proposal_type == proposal_type


def test_task_assist_proposals_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        StartAssistProposal(
            proposal_type="start",
            summary="先做一步",
            starter_step=_draft(),
            roadmap=[],
        )


def test_apply_request_does_not_accept_arbitrary_task_patch():
    with pytest.raises(ValidationError):
        TaskAssistApplyRequest(selected_option_id=None, title="越权修改")
