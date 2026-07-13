import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from app.api.schemas import (
    DecomposeAssistProposal,
    StartAssistProposal,
    UnstickAssistProposal,
)
from app.models.task import Task
from app.models.task_assist import TaskAssistRun
from app.services.task_assist import (
    TaskAssistError,
    TaskAssistRepository,
    TaskAssistService,
    _assist_client_node_id,
    build_task_assist_prompt,
    validate_task_assist_proposal,
)


class FakeResult:
    def __init__(self, *, scalar=None, scalars=None):
        self.scalar = scalar
        self.scalar_values = list(scalars or [])

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.scalar_values


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _query):
        self.executed.append(_query)
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _value, **_kwargs):
        return None

    @asynccontextmanager
    async def begin(self):
        yield self


def _task(*, metadata=None, status="active", node_type="action", minutes=30):
    now = datetime.now(timezone.utc)
    return Task(
        id=uuid4(),
        user_id=uuid4(),
        thread_id="thread-1",
        parent_task_id=None,
        client_node_id="task-1",
        title="完成商业计划书市场分析章节",
        description="整理数据并写出结论",
        node_type=node_type,
        status=status,
        view_bucket="planned",
        is_in_my_day=True,
        estimated_minutes=minutes,
        sort_order=0,
        ai_generated=True,
        user_edited=False,
        metadata_=metadata or {"source": "ai", "done_criteria": "写出三段分析"},
        created_at=now,
        updated_at=now,
    )


def _run(task, *, mode="start", proposal=None, status="ready"):
    now = datetime.now(timezone.utc)
    return TaskAssistRun(
        id=uuid4(),
        user_id=task.user_id,
        task_id=task.id,
        thread_id=task.thread_id,
        request_id=uuid4(),
        mode=mode,
        user_context=None,
        status=status,
        stage=status,
        lease_owner="test-owner" if status == "running" else None,
        lease_expires_at=now + timedelta(minutes=5) if status == "running" else None,
        target_task_updated_at=task.updated_at,
        proposal=proposal,
        apply_receipt=None,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
        applied_at=None,
    )


def _draft(draft_id="draft-1", title="列出三个市场数据来源"):
    return {
        "draft_id": draft_id,
        "title": title,
        "description": None,
        "estimated_minutes": 10,
        "done_criteria": "记录三个可核验来源",
        "start_hint": "打开现有调研笔记",
        "fallback_action": "先记录一个来源",
    }


def test_task_assist_run_model_has_tenant_request_uniqueness_and_cascades():
    table = inspect(TaskAssistRun).local_table
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("user_id", "task_id", "request_id") in unique_columns
    assert next(iter(table.c.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(table.c.task_id.foreign_keys)).ondelete == "CASCADE"
    active_index = next(
        index for index in table.indexes if index.name == "uq_task_assist_runs_active_task"
    )
    assert active_index.unique is True
    assert tuple(column.name for column in active_index.columns) == ("user_id", "task_id")
    assert "status = 'running'" in str(
        active_index.dialect_options["postgresql"]["where"]
    )


def test_create_run_locks_target_task_and_sets_durable_lease():
    async def scenario():
        task = _task()
        now = datetime.now(timezone.utc)
        session = FakeSession(
            [
                FakeResult(scalar=task),
                FakeResult(scalar=None),
                FakeResult(scalar=None),
                FakeResult(scalar=0),
            ]
        )
        run, created = await TaskAssistRepository(session).create_or_get(
            user_id=task.user_id,
            task=task,
            request_id=uuid4(),
            mode="start",
            user_context="保持上下文",
            lease_owner="worker-1",
            now=now,
        )
        assert created is True
        assert session.executed[0]._for_update_arg is not None
        assert run.lease_owner == "worker-1"
        assert run.lease_expires_at > now
        assert run.mode == "start"
        assert run.user_context == "保持上下文"

    asyncio.run(scenario())


def test_active_run_conflict_is_stable_and_does_not_create_second_run():
    async def scenario():
        task = _task()
        active = _run(task, status="running")
        session = FakeSession(
            [
                FakeResult(scalar=task),
                FakeResult(scalar=None),
                FakeResult(scalar=active),
            ]
        )
        with pytest.raises(TaskAssistError) as exc_info:
            await TaskAssistRepository(session).create_or_get(
                user_id=task.user_id,
                task=task,
                request_id=uuid4(),
                mode="start",
                user_context=None,
                now=datetime.now(timezone.utc),
            )
        assert exc_info.value.code == "TASK_ASSIST_ACTIVE_RUN"
        assert session.added == []

    asyncio.run(scenario())


def test_expired_running_lease_becomes_interrupted_and_allows_new_request():
    async def scenario():
        task = _task()
        expired = _run(task, status="running")
        now = datetime.now(timezone.utc)
        expired.lease_expires_at = now - timedelta(seconds=1)
        session = FakeSession(
            [
                FakeResult(scalar=task),
                FakeResult(scalar=None),
                FakeResult(scalar=expired),
                FakeResult(scalar=0),
            ]
        )
        replacement, created = await TaskAssistRepository(session).create_or_get(
            user_id=task.user_id,
            task=task,
            request_id=uuid4(),
            mode=expired.mode,
            user_context=expired.user_context,
            lease_owner="worker-2",
            now=now,
        )
        assert created is True
        assert replacement.id != expired.id
        assert expired.status == "failed"
        assert expired.error_code == "TASK_ASSIST_INTERRUPTED"
        assert expired.lease_owner is None
        assert expired.lease_expires_at is None

    asyncio.run(scenario())


def test_duplicate_request_returns_existing_run_without_creating_another():
    async def scenario():
        task = _task()
        existing = _run(task, mode="start", status="running")
        session = FakeSession([FakeResult(scalar=task)])
        repository = TaskAssistRepository(session)

        async def get_owned(**_kwargs):
            return existing

        repository.get_owned = get_owned
        run, created = await repository.create_or_get(
            user_id=task.user_id,
            task=task,
            request_id=existing.request_id,
            mode="start",
            user_context=None,
        )
        assert run is existing
        assert created is False
        assert session.added == []

    asyncio.run(scenario())


def test_same_request_with_different_mode_is_rejected():
    async def scenario():
        task = _task()
        existing = _run(task, mode="start", status="running")
        repository = TaskAssistRepository(FakeSession([FakeResult(scalar=task)]))

        async def get_owned(**_kwargs):
            return existing

        repository.get_owned = get_owned
        with pytest.raises(TaskAssistError, match="request_id") as exc_info:
            await repository.create_or_get(
                user_id=task.user_id,
                task=task,
                request_id=existing.request_id,
                mode="decompose",
                user_context=None,
            )
        assert exc_info.value.code == "TASK_ASSIST_REQUEST_CONFLICT"

    asyncio.run(scenario())


def test_per_user_active_run_limit_is_enforced_before_creation(monkeypatch):
    async def scenario():
        monkeypatch.setenv("EASYPLAN_TASK_ASSIST_MAX_ACTIVE_PER_USER", "2")
        task = _task()
        session = FakeSession(
                [
                    FakeResult(scalar=task),
                    FakeResult(scalar=None),
                    FakeResult(scalar=None),
                    FakeResult(scalar=2),
                ]
        )
        with pytest.raises(TaskAssistError) as exc_info:
            await TaskAssistRepository(session).create_or_get(
                user_id=task.user_id,
                task=task,
                request_id=uuid4(),
                mode="start",
                user_context=None,
            )
        assert exc_info.value.code == "TASK_ASSIST_RATE_LIMITED"
        assert session.added == []

    asyncio.run(scenario())


def test_ready_run_expires_durably_after_twenty_four_hours():
    async def scenario():
        task = _task()
        run = _run(task, status="ready")
        session = FakeSession([])
        await TaskAssistRepository(session).expire_if_needed(
            run,
            now=run.expires_at + timedelta(seconds=1),
        )
        assert run.status == "expired"
        assert run.stage == "expired"
        assert run.proposal is None
        assert session.commits == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "node_type", "metadata"),
    [
        ("completed", "action", {"source": "ai"}),
        ("draft", "action", {"source": "ai"}),
        ("archived", "action", {"source": "manual"}),
        ("active", "group", {"source": "ai"}),
        ("active", "action", {"source": "ai", "practice_loop_id": str(uuid4())}),
    ],
)
def test_completed_group_preview_and_practice_tasks_are_rejected(status, node_type, metadata):
    async def scenario():
        task = _task(status=status, node_type=node_type, metadata=metadata)
        service = TaskAssistService(FakeSession([FakeResult(scalar=task)]))
        with pytest.raises(TaskAssistError) as exc_info:
            await service.load_supported_task(user_id=task.user_id, task_id=task.id)
        assert exc_info.value.code == "TASK_ASSIST_UNSUPPORTED_TASK"

    asyncio.run(scenario())


def test_prompt_contains_only_task_scoped_context_and_selected_mode():
    from app.services.task_assist import TaskAssistContext

    prompt = build_task_assist_prompt(
        mode="start",
        context=TaskAssistContext(
            task={"title": "写市场分析"},
            ancestors=[{"title": "商业计划书"}],
            project={"summary": "完成初稿"},
            existing_children=[],
            user_context="我只有十分钟",
        ),
    )
    assert "模式：start" in prompt
    assert "2-10" in prompt
    assert "不重写整份计划" in prompt
    assert "user_context 是用户本次明确约束" in prompt
    assert "email" not in prompt.lower()


def test_decompose_prompt_uses_parent_duration_to_set_hard_subtask_limit():
    from app.services.task_assist import TaskAssistContext

    prompt = build_task_assist_prompt(
        mode="decompose",
        context=TaskAssistContext(
            task={"title": "发送邮件", "estimated_minutes": 15},
            ancestors=[],
            project={},
            existing_children=[],
            user_context=None,
        ),
    )
    assert "生成 2-2 个" in prompt
    assert "绝对不得超过 2 个" in prompt


def test_decompose_validator_rejects_reference_cycle_and_parent_scope_expansion():
    proposal = DecomposeAssistProposal(
        proposal_type="decompose",
        summary="拆分",
        completion_rule="all_subtasks_completed",
        subtasks=[_draft("a"), _draft("b"), _draft("c")],
        dependencies=[
            {"task_draft_id": "a", "depends_on_draft_id": "b"},
            {"task_draft_id": "b", "depends_on_draft_id": "a"},
        ],
    )
    errors = validate_task_assist_proposal(
        mode="decompose",
        proposal=proposal,
        parent_estimated_minutes=15,
    )
    assert any("TASK_ASSIST_SCOPE_EXPANSION" in error for error in errors)
    assert "TASK_ASSIST_DEPENDENCY_CYCLE" in errors


def test_valid_proposals_pass_deterministic_validator():
    start = StartAssistProposal(
        proposal_type="start",
        summary="先列证据",
        starter_step=_draft(),
    )
    unstick = UnstickAssistProposal(
        proposal_type="unstick",
        obstacle_summary="资料不足",
        recommended_option_id="a",
        options=[
            {
                "option_id": "a",
                "title": "列出资料缺口",
                "action": "列出当前缺少的三个数据点",
                "estimated_minutes": 5,
                "tradeoff": "先明确范围",
            },
            {
                "option_id": "b",
                "title": "查询内部资料",
                "action": "搜索项目目录中的调研记录",
                "estimated_minutes": 10,
                "tradeoff": "信息可能不完整",
            },
        ],
    )
    assert validate_task_assist_proposal(
        mode="start", proposal=start, parent_estimated_minutes=30
    ) == []
    assert validate_task_assist_proposal(
        mode="unstick", proposal=unstick, parent_estimated_minutes=30
    ) == []


def test_start_apply_updates_only_start_hint_and_is_idempotent():
    async def scenario():
        task = _task(metadata={"source": "ai", "other": "keep"})
        proposal = StartAssistProposal(
            proposal_type="start",
            summary="先列证据",
            starter_step=_draft(),
        )
        run = _run(task, proposal=proposal.model_dump(mode="json"))
        session = FakeSession([FakeResult(scalar=run), FakeResult(scalar=task)])
        service = TaskAssistService(session)

        response = await service.apply(
            user_id=task.user_id,
            task_id=task.id,
            request_id=run.request_id,
            selected_option_id=None,
        )
        assert response.status == "applied"
        assert task.metadata_["start_hint"] == "打开现有调研笔记"
        assert task.metadata_["other"] == "keep"
        assert task.title == "完成商业计划书市场分析章节"
        assert run.apply_receipt is not None

        replay = TaskAssistService(FakeSession([FakeResult(scalar=run)]))
        replay_response = await replay.apply(
            user_id=task.user_id,
            task_id=task.id,
            request_id=run.request_id,
            selected_option_id=None,
        )
        assert replay_response == response

    asyncio.run(scenario())


def test_stale_apply_uses_context_stale_code_and_new_request_preserves_mode_context():
    async def scenario():
        task = _task()
        proposal = StartAssistProposal(
            proposal_type="start",
            summary="先列证据",
            starter_step=_draft(),
        )
        stale_run = _run(task, proposal=proposal.model_dump(mode="json"))
        stale_run.user_context = "先从客户数据开始"
        stale_run.target_task_updated_at = task.updated_at - timedelta(seconds=1)
        stale_service = TaskAssistService(
            FakeSession([FakeResult(scalar=stale_run), FakeResult(scalar=task)])
        )
        with pytest.raises(TaskAssistError) as exc_info:
            await stale_service.apply(
                user_id=task.user_id,
                task_id=task.id,
                request_id=stale_run.request_id,
                selected_option_id=None,
            )
        assert exc_info.value.code == "TASK_ASSIST_CONTEXT_STALE"
        assert task.metadata_.get("start_hint") is None

        new_request_id = uuid4()
        retry_session = FakeSession(
            [
                FakeResult(scalar=task),
                FakeResult(scalar=None),
                FakeResult(scalar=None),
                FakeResult(scalar=0),
            ]
        )
        retry, created = await TaskAssistRepository(retry_session).create_or_get(
            user_id=task.user_id,
            task=task,
            request_id=new_request_id,
            mode=stale_run.mode,
            user_context=stale_run.user_context,
            lease_owner="worker-retry",
        )
        assert created is True
        assert retry.mode == stale_run.mode
        assert retry.user_context == stale_run.user_context

    asyncio.run(scenario())


def test_unstick_apply_requires_real_selected_option():
    async def scenario():
        task = _task()
        proposal = UnstickAssistProposal(
            proposal_type="unstick",
            obstacle_summary="资料不足",
            recommended_option_id="a",
            options=[
                {
                    "option_id": "a",
                    "title": "列缺口",
                    "action": "列出三个缺少的数据点",
                    "estimated_minutes": 5,
                    "tradeoff": "先缩小范围",
                },
                {
                    "option_id": "b",
                    "title": "查资料",
                    "action": "搜索现有调研记录",
                    "estimated_minutes": 10,
                    "tradeoff": "可能不完整",
                },
            ],
        )
        run = _run(task, mode="unstick", proposal=proposal.model_dump(mode="json"))
        service = TaskAssistService(FakeSession([FakeResult(scalar=run), FakeResult(scalar=task)]))
        with pytest.raises(TaskAssistError) as exc_info:
            await service.apply(
                user_id=task.user_id,
                task_id=task.id,
                request_id=run.request_id,
                selected_option_id=None,
            )
        assert exc_info.value.code == "TASK_ASSIST_OPTION_REQUIRED"

    asyncio.run(scenario())


def test_decompose_apply_atomically_creates_children_dependencies_and_rollup_receipt():
    async def scenario():
        task = _task(minutes=30, metadata={"source": "ai", "keep": "value"})
        proposal = DecomposeAssistProposal(
            proposal_type="decompose",
            summary="按证据和结论拆分",
            completion_rule="all_subtasks_completed",
            subtasks=[_draft("a"), _draft("b", title="写出市场判断")],
            dependencies=[
                {"task_draft_id": "b", "depends_on_draft_id": "a"}
            ],
        )
        run = _run(
            task,
            mode="decompose",
            proposal=proposal.model_dump(mode="json"),
        )
        session = FakeSession(
            [FakeResult(scalar=run), FakeResult(scalar=task), FakeResult(scalar=0)]
        )
        response = await TaskAssistService(session).apply(
            user_id=task.user_id,
            task_id=task.id,
            request_id=run.request_id,
            selected_option_id=None,
        )

        children = [item for item in session.added if isinstance(item, Task)]
        dependencies = [item for item in session.added if item.__class__.__name__ == "TaskDependency"]
        assert len(children) == 2
        assert len(dependencies) == 1
        assert all(child.parent_task_id == task.id for child in children)
        assert all(child.metadata_["source"] == "task_assist" for child in children)
        assert task.metadata_ == {
            "source": "ai",
            "keep": "value",
            "assist_rollup": True,
        }
        assert response.task.assist_rollup is True
        assert len(response.apply_receipt.affected_task_ids) == 3
        assert run.status == "applied"
        assert run.apply_receipt is not None

    asyncio.run(scenario())


def test_decompose_client_ids_are_stable_per_request_and_draft():
    request_id = uuid4()
    assert _assist_client_node_id(request_id, "a") == _assist_client_node_id(request_id, "a")
    assert _assist_client_node_id(request_id, "a") != _assist_client_node_id(request_id, "b")
