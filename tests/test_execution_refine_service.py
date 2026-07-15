import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.api.schemas import (
    ExecutionRefineProposal,
    ExecutionRefineRequest,
    SetMyDayOperation,
    TaskTree,
)
from app.models.execution_refine import ExecutionRefineRun
from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread
from app.services.execution_refine import (
    ExecutionRefineError,
    ExecutionRefineRepository,
    ExecutionRefineService,
    build_execution_refine_prompt,
    build_execution_refine_scope,
    canonical_scope_fingerprint,
    execution_refine_client_node_id,
    execution_refine_enabled,
    normalize_execution_refine_proposal,
    validate_execution_refine_proposal,
)
from app.services.phase_planning import calculate_phase_progress
from app.services.llm_service import LLMStructuredOutputError


class FakeResult:
    def __init__(self, *, scalar=None, scalars=None, rowcount=0):
        self.scalar = scalar
        self.scalar_values = list(scalars or [])
        self.rowcount = rowcount

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

    async def execute(self, query):
        self.executed.append(query)
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

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


class RollbackFakeSession(FakeSession):
    def __init__(self, results, tracked):
        super().__init__(results)
        self.tracked = list(tracked)

    @asynccontextmanager
    async def begin(self):
        snapshots = []
        for value in self.tracked:
            state = {
                key: copy.deepcopy(item)
                for key, item in vars(value).items()
                if key != "_sa_instance_state"
            }
            snapshots.append((value, state))
        try:
            yield self
        except Exception:
            for value, state in snapshots:
                for key in list(vars(value)):
                    if key != "_sa_instance_state" and key not in state:
                        delattr(value, key)
                for key, item in state.items():
                    setattr(value, key, item)
            self.added.clear()
            raise


def _task_tree(*, planning_context=None):
    return {
        "root": {
            "client_node_id": "root",
            "title": "完成演示项目",
            "description": None,
            "verb": "完成",
            "estimated_minutes": 60,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "task-1",
                    "title": "写出演示稿的三条核心结论",
                    "description": "根据现有资料形成结论",
                    "verb": "写出",
                    "estimated_minutes": 30,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                    "done_criteria": "写出三条可用于演示的结论",
                    "start_hint": "打开现有调研笔记",
                    "fallback_action": "先写出第一条结论",
                },
                {
                    "client_node_id": "task-2",
                    "title": "整理五页演示稿",
                    "description": None,
                    "verb": "整理",
                    "estimated_minutes": 30,
                    "node_type": "action",
                    "depends_on": ["task-1"],
                    "children": [],
                    "done_criteria": "保存一份包含五页内容的演示稿",
                    "start_hint": "打开演示模板",
                    "fallback_action": "先完成标题页和结论页",
                },
            ],
            "done_criteria": None,
            "start_hint": None,
            "fallback_action": None,
        },
        "summary": "先完成演示项目的当前行动",
        "assumptions": [],
        "planning_context": planning_context,
        "strategy_context": None,
    }


def _planning_context(*, schema_version=1):
    return {
        "schema_version": schema_version,
        "intent_type": "long_term_growth",
        "time_horizon": "months",
        "roadmap": [
            {
                "phase_id": "phase-1",
                "order": 1,
                "title": "当前启动",
                "objective": "形成初稿",
                "status": "current",
            },
            {
                "phase_id": "phase-2",
                "order": 2,
                "title": "强化表达",
                "objective": "完善表达",
                "status": "planned",
            },
            {
                "phase_id": "phase-3",
                "order": 3,
                "title": "最终交付",
                "objective": "完成交付",
                "status": "planned",
            },
        ],
        "current_phase": {
            "phase_id": "phase-1",
            "title": "当前启动",
            "objective": "形成初稿",
            "completion_rule": (
                "long_term_execution_gate"
                if schema_version == 2
                else "all_ai_actions_completed"
            ),
            "estimated_duration_weeks": 2 if schema_version == 2 else None,
        },
        "next_action_client_node_id": "task-1",
        "practice_loops": [],
        "outcome_checkpoints": (
            [
                {
                    "checkpoint_id": "checkpoint-1",
                    "title": "保存演示初稿",
                    "evidence_type": "artifact",
                    "unit": None,
                    "operator": "exists",
                    "target_value": None,
                }
            ]
            if schema_version == 2
            else []
        ),
        "phase_gate": (
            {"process_threshold": 0.8, "outcome_rule": "all_required"}
            if schema_version == 2
            else None
        ),
    }


def _thread(*, task_tree=None, user_id=None):
    now = datetime.now(timezone.utc)
    return AgentThread(
        id=uuid4(),
        user_id=user_id or uuid4(),
        thread_id="thread-1",
        intent_text="完成演示项目",
        status="completed",
        current_node="completed",
        next_nodes=[],
        lease_owner=None,
        lease_expires_at=None,
        interrupt_payload=None,
        latest_checkpoint_id=None,
        task_tree=task_tree or _task_tree(),
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        interrupted_at=None,
        completed_at=now,
        expires_at=None,
    )


def _task(
    *,
    user_id,
    task_id=None,
    client_node_id="task-1",
    parent_task_id=None,
    status="active",
    source="ai",
    phase_id=None,
    ai_generated=True,
    node_type="action",
    is_in_my_day=False,
    minutes=30,
    sort_order=0,
    metadata=None,
):
    now = datetime.now(timezone.utc)
    task_metadata = {
        "source": source,
        "phase_id": phase_id,
        "done_criteria": "写出三条可用于演示的结论",
        "start_hint": "打开现有调研笔记",
        "fallback_action": "先写第一条",
        **(metadata or {}),
    }
    return Task(
        id=task_id or uuid4(),
        user_id=user_id,
        thread_id="thread-1",
        parent_task_id=parent_task_id,
        client_node_id=client_node_id,
        title=(
            "写出演示稿的三条核心结论"
            if client_node_id == "task-1"
            else "整理五页演示稿"
        ),
        description="根据现有资料形成结论",
        node_type=node_type,
        status=status,
        view_bucket="planned",
        is_in_my_day=is_in_my_day,
        estimated_minutes=minutes,
        sort_order=sort_order,
        ai_generated=ai_generated,
        user_edited=not ai_generated,
        metadata_=task_metadata,
        created_at=now,
        updated_at=now,
    )


def _request(mode="progress_recovery", **kwargs):
    return ExecutionRefineRequest(request_id=uuid4(), mode=mode, **kwargs)


def _scope(*, planning=False, tasks=None, request=None):
    thread = _thread(task_tree=_task_tree(planning_context=_planning_context() if planning else None))
    user_id = thread.user_id
    task_values = tasks or [
        _task(
            user_id=user_id,
            client_node_id="task-1",
            phase_id="phase-1" if planning else None,
            sort_order=0,
        ),
        _task(
            user_id=user_id,
            client_node_id="task-2",
            phase_id="phase-1" if planning else None,
            sort_order=1,
        ),
    ]
    return build_execution_refine_scope(
        thread=thread,
        tasks=task_values,
        dependencies=[],
        phase_reviews=[],
        request=request or _request(),
    )


def _valid_proposal(scope, request, *, operations=None, focus=None, focus_minutes=0, buffer=0):
    first_id = next(iter(scope.task_records))
    return ExecutionRefineProposal.model_validate(
        {
            "schema_version": 1,
            "proposal_type": "execution_refine",
            "mode": request.mode,
            "summary": "调整当前执行范围",
            "user_facing_reasons": ["优先完成当前最关键任务"],
            "preserved_constraints": ["已完成工作和历史保持不变"],
            "operations": operations
            or [
                {
                    "operation_type": "update_task",
                    "task_id": first_id,
                    "changes": {"estimated_minutes": 20},
                    "reason": "缩短当前任务",
                }
            ],
            "focus_task_ids": focus or [],
            "estimated_focus_minutes": focus_minutes,
            "buffer_minutes": buffer,
            "warnings": [],
        }
    )


def _run(thread, request, scope, *, status="running"):
    now = datetime.now(timezone.utc)
    return ExecutionRefineRun(
        id=uuid4(),
        user_id=thread.user_id,
        thread_id=thread.thread_id,
        request_id=request.request_id,
        mode=request.mode,
        input_context=request.model_dump(mode="json"),
        scope_snapshot=scope.snapshot,
        scope_fingerprint=scope.fingerprint,
        status=status,
        stage="queued" if status == "running" else status,
        proposal=None,
        apply_receipt=None,
        error_code=None,
        error_message=None,
        lease_owner="worker-1" if status == "running" else None,
        lease_expires_at=now + timedelta(minutes=5) if status == "running" else None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
        applied_at=None,
        cancelled_at=None,
    )


def test_execution_refine_run_model_has_request_identity_and_active_partial_index():
    table = inspect(ExecutionRefineRun).local_table
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("user_id", "thread_id", "request_id") in unique_columns
    active_index = next(
        index
        for index in table.indexes
        if index.name == "uq_execution_refine_runs_active_thread"
    )
    assert active_index.unique is True
    assert tuple(column.name for column in active_index.columns) == (
        "user_id",
        "thread_id",
    )
    assert "status = 'running'" in str(
        active_index.dialect_options["postgresql"]["where"]
    )
    assert next(iter(table.c.thread_id.foreign_keys)).ondelete == "CASCADE"
    ddl = str(CreateIndex(active_index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE INDEX" in ddl
    assert "WHERE status = 'running'" in ddl


def test_execution_refine_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("EASYPLAN_EXECUTION_REFINE_ENABLED", raising=False)
    assert execution_refine_enabled() is False


def test_create_or_get_locks_thread_and_creates_durable_lease():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        thread = _thread(user_id=uuid4())
        session = FakeSession(
            [
                FakeResult(scalar=thread),
                FakeResult(scalar=None),
                FakeResult(scalar=None),
                FakeResult(scalar=0),
            ]
        )
        run, created = await ExecutionRefineRepository(session).create_or_get(
            user_id=thread.user_id,
            thread_id=thread.thread_id,
            request=request,
            scope=scope,
            lease_owner="worker-1",
        )
        assert created is True
        assert session.executed[0]._for_update_arg is not None
        assert run.lease_owner == "worker-1"
        assert run.expires_at - run.created_at <= timedelta(hours=24, seconds=1)
        assert run.scope_fingerprint == scope.fingerprint

    asyncio.run(scenario())


def test_duplicate_request_id_returns_same_run():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        thread = _thread(user_id=uuid4())
        existing = _run(thread, request, scope)
        session = FakeSession([FakeResult(scalar=thread), FakeResult(scalar=existing)])
        run, created = await ExecutionRefineRepository(session).create_or_get(
            user_id=thread.user_id,
            thread_id=thread.thread_id,
            request=request,
            scope=scope,
        )
        assert run is existing
        assert created is False
        assert session.added == []

    asyncio.run(scenario())


def test_active_run_conflict_is_stable_and_expired_lease_releases_slot():
    async def conflict_scenario():
        request = _request()
        scope = _scope(request=request)
        thread = _thread(user_id=uuid4())
        active = _run(thread, _request(), scope)
        session = FakeSession(
            [FakeResult(scalar=thread), FakeResult(scalar=None), FakeResult(scalar=active)]
        )
        with pytest.raises(ExecutionRefineError) as exc_info:
            await ExecutionRefineRepository(session).create_or_get(
                user_id=thread.user_id,
                thread_id=thread.thread_id,
                request=request,
                scope=scope,
            )
        assert exc_info.value.code == "EXECUTION_REFINE_ACTIVE_RUN"
        assert session.added == []

    async def expired_scenario():
        request = _request()
        scope = _scope(request=request)
        thread = _thread(user_id=uuid4())
        active = _run(thread, _request(), scope)
        active.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session = FakeSession(
            [
                FakeResult(scalar=thread),
                FakeResult(scalar=None),
                FakeResult(scalar=active),
                FakeResult(scalar=0),
            ]
        )
        run, created = await ExecutionRefineRepository(session).create_or_get(
            user_id=thread.user_id,
            thread_id=thread.thread_id,
            request=request,
            scope=scope,
        )
        assert created is True
        assert active.status == "failed"
        assert active.error_code == "EXECUTION_REFINE_INTERRUPTED"
        assert run.status == "running"

    asyncio.run(conflict_scenario())
    asyncio.run(expired_scenario())


def test_terminal_cancel_transition_is_idempotent():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        thread = _thread(user_id=uuid4())
        run = _run(thread, request, scope)
        session = FakeSession([])
        repository = ExecutionRefineRepository(session)
        first = await repository.cancel(run)
        second = await repository.cancel(run)
        assert first is second
        assert run.status == "cancelled"
        assert run.cancelled_at is not None

    asyncio.run(scenario())


def test_repository_returns_non_enumerating_not_found_for_foreign_thread():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        session = FakeSession([FakeResult(scalar=None)])
        with pytest.raises(ExecutionRefineError) as exc_info:
            await ExecutionRefineRepository(session).create_or_get(
                user_id=uuid4(),
                thread_id="foreign-thread",
                request=request,
                scope=scope,
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "EXECUTION_REFINE_SCOPE_FORBIDDEN"
        assert "foreign" not in exc_info.value.message

    asyncio.run(scenario())


def test_repository_persists_only_safe_failure_summary():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        thread = _thread(user_id=uuid4())
        run = _run(thread, request, scope)
        session = FakeSession([])
        saved = await ExecutionRefineRepository(session).fail(
            run,
            code="PROVIDER_FAILURE",
            message="SELECT secret FROM users; traceback=/srv/app.py:10",
            lease_owner="worker-1",
        )
        assert saved is True
        assert run.error_message == "本次计划调整暂时未完成，请稍后重试。"
        assert "SELECT" not in run.error_message

    asyncio.run(scenario())


def test_scope_protects_history_completed_manual_assist_and_practice_tasks():
    thread = _thread(
        task_tree=_task_tree(planning_context=_planning_context(schema_version=2))
    )
    current = _task(
        user_id=thread.user_id,
        client_node_id="task-1",
        phase_id="phase-1",
    )
    historical = _task(
        user_id=thread.user_id,
        client_node_id="task-2",
        phase_id="phase-2",
        sort_order=1,
    )
    completed = _task(
        user_id=thread.user_id,
        client_node_id="completed",
        phase_id="phase-1",
        status="completed",
        sort_order=2,
    )
    manual = _task(
        user_id=thread.user_id,
        client_node_id="manual",
        source="manual",
        phase_id="phase-1",
        ai_generated=False,
        sort_order=3,
    )
    assist = _task(
        user_id=thread.user_id,
        client_node_id="assist",
        source="task_assist",
        phase_id="phase-1",
        sort_order=4,
    )
    practice = _task(
        user_id=thread.user_id,
        client_node_id="practice",
        phase_id="phase-1",
        metadata={"practice_loop_id": str(uuid4())},
        sort_order=5,
    )
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=[current, historical, completed, manual, assist, practice],
        dependencies=[],
        phase_reviews=[],
        request=_request(),
    )
    records = scope.task_records
    assert records[str(current.id)]["capabilities"]["update_task"] is True
    assert records[str(historical.id)]["protected_reason"] == "historical_or_future_phase"
    assert records[str(completed.id)]["protected_reason"] == "completed_or_archived"
    assert records[str(manual.id)]["capabilities"] == {
        "update_task": False,
        "reorder": True,
        "set_my_day": True,
        "add_child": False,
    }
    assert records[str(assist.id)]["protected_reason"] == "task_assist_child"
    assert records[str(practice.id)]["protected_reason"] == "practice_occurrence"


def test_assist_rollup_parent_is_protected_anchor_but_can_reorder_and_toggle_my_day():
    thread = _thread()
    parent = _task(
        user_id=thread.user_id,
        metadata={"assist_rollup": True},
    )
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=[parent],
        dependencies=[],
        phase_reviews=[],
        request=_request(),
    )
    capabilities = scope.task_records[str(parent.id)]["capabilities"]
    assert scope.task_records[str(parent.id)]["protected_reason"] == "assist_rollup_anchor"
    assert capabilities["update_task"] is False
    assert capabilities["reorder"] is True
    assert capabilities["set_my_day"] is True


def test_scope_rejects_task_references_outside_owned_thread():
    thread = _thread()
    request = _request(
        mode="context_change",
        priority_task_ids=[uuid4()],
    )
    with pytest.raises(ExecutionRefineError) as exc_info:
        build_execution_refine_scope(
            thread=thread,
            tasks=[],
            dependencies=[],
            phase_reviews=[],
            request=request,
        )
    assert exc_info.value.code == "EXECUTION_REFINE_SCOPE_FORBIDDEN"


def test_scope_fingerprint_is_canonical_and_covers_task_state_metadata_and_dependencies():
    thread = _thread()
    first = _task(user_id=thread.user_id, client_node_id="task-1", sort_order=0)
    second = _task(user_id=thread.user_id, client_node_id="task-2", sort_order=1)
    request = _request()

    def build(tasks, dependencies=()):
        return build_execution_refine_scope(
            thread=thread,
            tasks=tasks,
            dependencies=dependencies,
            phase_reviews=[],
            request=request,
        )

    baseline = build([first, second])
    equivalent = build([second, first])
    assert baseline.fingerprint == equivalent.fingerprint
    first.status = "completed"
    assert baseline.fingerprint != build([first, second]).fingerprint
    first.status = "active"
    first.metadata_ = {**first.metadata_, "new_constraint": "changed"}
    assert baseline.fingerprint != build([first, second]).fingerprint
    first.metadata_.pop("new_constraint")
    dependency = TaskDependency(
        id=uuid4(),
        task_id=second.id,
        depends_on_task_id=first.id,
        created_at=datetime.now(timezone.utc),
    )
    assert baseline.fingerprint != build([first, second], [dependency]).fingerprint
    first.metadata_ = {**first.metadata_, "phase_id": "changed-phase"}
    assert baseline.fingerprint != build([first, second]).fingerprint
    first.metadata_["phase_id"] = None
    changed_tree = dict(thread.task_tree)
    changed_tree["summary"] = "changed committed tree"
    thread.task_tree = changed_tree
    assert baseline.fingerprint != build([first, second]).fingerprint


def test_canonical_scope_fingerprint_ignores_mapping_order():
    assert canonical_scope_fingerprint({"a": 1, "b": {"x": 2}}) == canonical_scope_fingerprint(
        {"b": {"x": 2}, "a": 1}
    )


def test_prompt_contains_only_bounded_contract_and_structured_repair_feedback():
    request = _request(mode="time_budget", available_minutes=45)
    scope = _scope(request=request)
    base = build_execution_refine_prompt(request=request, scope=scope)
    assert "update_task/add_task/reorder_siblings/set_my_day" in base
    assert "禁止 delete/archive/status/phase" in base
    assert "Bounded Scope JSON" in base
    assert str(request.request_id) in base

    invalid = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": next(iter(scope.task_records)),
                "changes": {"estimated_minutes": 20},
                "reason": "缩小",
            }
        ],
        buffer=3,
    )
    issues = validate_execution_refine_proposal(
        proposal=invalid,
        request=request,
        scope=scope,
    )
    repair = build_execution_refine_prompt(
        request=request,
        scope=scope,
        repair_issues=issues,
        repair_base_proposal=invalid,
    )
    assert "只修复下列无效 operation" in repair
    assert any(issue.error_code in repair for issue in issues)
    assert "available_minutes" in repair
    assert '"buffer_minutes":7' in repair
    assert '"protected_commitment_minutes":0' in repair
    assert '"remaining_focus_minutes":38' in repair
    assert "禁止 add_task" in repair
    assert "禁止引入新的 focus_task_id" in repair
    assert "上一次 schema-valid proposal" in repair


def test_time_budget_repair_cannot_add_expand_or_replace_candidates():
    request = _request(mode="time_budget", available_minutes=240)
    scope = _scope(request=request)
    task_ids = list(scope.task_records)
    base = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": task_ids[0],
                "changes": {"estimated_minutes": 20},
                "reason": "压缩候选任务",
            },
            {
                "operation_type": "set_my_day",
                "task_id": task_ids[0],
                "is_in_my_day": True,
                "reason": "加入本次 focus",
            },
        ],
        focus=[task_ids[0]],
        focus_minutes=20,
        buffer=20,
    )
    expanded = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": task_ids[0],
                "changes": {"estimated_minutes": 25},
                "reason": "错误扩大候选时长",
            },
            {
                "operation_type": "add_task",
                "draft_id": "new-capacity-task",
                "parent_task_id": None,
                "title": "新增一项容量任务",
                "estimated_minutes": 10,
                "done_criteria": "保存新增任务产出",
                "depends_on_refs": [],
                "reason": "错误新增任务",
            },
            {
                "operation_type": "set_my_day",
                "task_id": task_ids[1],
                "is_in_my_day": True,
                "reason": "错误替换候选",
            },
        ],
        focus=[task_ids[1]],
        focus_minutes=30,
        buffer=20,
    )

    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=expanded,
            request=request,
            scope=scope,
            repair_base_proposal=base,
        )
    }
    assert "EXECUTION_REFINE_REPAIR_ADD_FORBIDDEN" in codes
    assert "EXECUTION_REFINE_REPAIR_FOCUS_EXPANDED" in codes
    assert "EXECUTION_REFINE_REPAIR_CAPACITY_EXPANDED" in codes
    assert "EXECUTION_REFINE_REPAIR_MY_DAY_EXPANDED" in codes


def test_validator_rejects_syntactically_valid_protected_task_mutation():
    thread = _thread()
    completed = _task(
        user_id=thread.user_id,
        status="completed",
        client_node_id="task-1",
    )
    request = _request()
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=[completed],
        dependencies=[],
        phase_reviews=[],
        request=request,
    )
    proposal = _valid_proposal(scope, request)
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_MUTATION_FORBIDDEN" in codes


def test_validator_rejects_invalid_reorder_set_and_dependency_cycle():
    request = _request()
    scope = _scope(request=request)
    task_ids = list(scope.task_records)
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "reorder_siblings",
                "parent_task_id": None,
                "ordered_task_ids": [task_ids[0]],
                "reason": "遗漏 sibling",
            },
            {
                "operation_type": "add_task",
                "draft_id": "a",
                "parent_task_id": None,
                "title": "写出第一条恢复动作",
                "estimated_minutes": 10,
                "done_criteria": "写出一条动作",
                "depends_on_refs": ["b"],
                "reason": "补齐动作",
            },
            {
                "operation_type": "add_task",
                "draft_id": "b",
                "parent_task_id": None,
                "title": "记录第二条恢复动作",
                "estimated_minutes": 10,
                "done_criteria": "记录一条动作",
                "depends_on_refs": ["a"],
                "reason": "补齐动作",
            },
        ],
    )
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_SIBLING_SET_INVALID" in codes
    assert "EXECUTION_REFINE_DEPENDENCY_CYCLE" in codes


def test_validator_reuses_action_quality_for_changed_and_new_actions():
    request = _request()
    scope = _scope(request=request)
    first_id = next(iter(scope.task_records))
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": first_id,
                "changes": {"title": "研究一下", "done_criteria": "完成任务"},
                "reason": "错误样本",
            }
        ],
    )
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_ACTION_QUALITY" in codes


@pytest.mark.parametrize(
    ("title", "done_criteria"),
    [("placeholder", "placeholder"), ("占位任务", "无操作")],
)
def test_validator_rejects_placeholder_action_content(title, done_criteria):
    request = _request()
    scope = _scope(request=request)
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "add_task",
                "draft_id": "placeholder-action",
                "parent_task_id": None,
                "title": title,
                "estimated_minutes": 5,
                "done_criteria": done_criteria,
                "reason": "temporary value",
            }
        ],
    )
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_ACTION_QUALITY" in codes


def test_validator_preserves_deadline_priority_and_blocked_constraints():
    base_scope = _scope()
    task_ids = list(base_scope.task_records)
    deadline = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
    request = _request(
        mode="context_change",
        new_deadline=deadline,
        priority_task_ids=[task_ids[0]],
        blocked_task_ids=[task_ids[1]],
    )
    scope = base_scope
    proposal = _valid_proposal(scope, request)
    invalid_codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_DEADLINE_CONSTRAINT_LOST" in invalid_codes

    valid = proposal.model_copy(
        update={
            "preserved_constraints": ["新截止日期保持为 2026-07-20"],
        }
    )
    assert validate_execution_refine_proposal(
        proposal=valid,
        request=request,
        scope=scope,
    ) == []


def test_time_budget_validator_recomputes_buffer_focus_and_protected_capacity():
    thread = _thread()
    focus_task = _task(
        user_id=thread.user_id,
        client_node_id="task-1",
        minutes=20,
        is_in_my_day=False,
    )
    protected = _task(
        user_id=thread.user_id,
        client_node_id="task-2",
        source="practice",
        minutes=10,
        is_in_my_day=True,
        sort_order=1,
        metadata={"practice_loop_id": str(uuid4())},
    )
    request = _request(mode="time_budget", available_minutes=45)
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=[focus_task, protected],
        dependencies=[],
        phase_reviews=[],
        request=request,
    )
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "set_my_day",
                "task_id": str(focus_task.id),
                "is_in_my_day": True,
                "reason": "加入今日聚焦",
            }
        ],
        focus=[focus_task.id],
        focus_minutes=20,
        buffer=7,
    )
    assert validate_execution_refine_proposal(
        proposal=proposal,
        request=request,
        scope=scope,
    ) == []

    invalid = proposal.model_copy(update={"buffer_minutes": 3})
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=invalid,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_BUFFER_INVALID" in codes


def test_time_budget_validator_rejects_estimate_inflation_to_fill_capacity():
    request = _request(mode="time_budget", available_minutes=240)
    scope = _scope(request=request)
    first_id = next(iter(scope.task_records))
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": first_id,
                "changes": {"estimated_minutes": 180},
                "reason": "填满预算",
            },
            {
                "operation_type": "set_my_day",
                "task_id": first_id,
                "is_in_my_day": True,
                "reason": "加入焦点",
            },
        ],
        focus=[UUID(first_id)],
        focus_minutes=180,
        buffer=20,
    )
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_CAPACITY_INFLATION" in codes


def test_time_budget_cannot_fabricate_capacity_when_protected_commitment_fills_day():
    thread = _thread()
    mutable = _task(user_id=thread.user_id, client_node_id="task-1", minutes=10)
    protected = _task(
        user_id=thread.user_id,
        client_node_id="task-2",
        source="practice",
        minutes=20,
        is_in_my_day=True,
        sort_order=1,
        metadata={"practice_loop_id": str(uuid4())},
    )
    request = _request(mode="time_budget", available_minutes=20)
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=[mutable, protected],
        dependencies=[],
        phase_reviews=[],
        request=request,
    )
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": str(mutable.id),
                "changes": {"estimated_minutes": 5},
                "reason": "准备更小替代动作",
            }
        ],
        buffer=3,
    )
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_CAPACITY_WARNING_REQUIRED" in codes

    warned = proposal.model_copy(update={"warnings": ["现有练习已占满可用时间"]})
    assert validate_execution_refine_proposal(
        proposal=warned,
        request=request,
        scope=scope,
    ) == []


def test_validator_uses_in_memory_copies_without_mutating_committed_scope():
    request = _request()
    scope = _scope(request=request)
    before = scope.task_tree.model_dump(mode="json")
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "add_task",
                "draft_id": "safe_add",
                "parent_task_id": None,
                "title": "记录三条恢复检查项",
                "estimated_minutes": 10,
                "done_criteria": "记录三条可核验检查项",
                "depends_on_refs": [],
                "reason": "补齐恢复检查",
            }
        ],
    )
    validate_execution_refine_proposal(
        proposal=proposal,
        request=request,
        scope=scope,
    )
    assert scope.task_tree.model_dump(mode="json") == before


def test_normalizer_canonicalizes_derived_capacity_fields_and_removes_noops():
    request = _request(mode="time_budget", available_minutes=45)
    scope = _scope(request=request)
    task_ids = list(scope.task_records)
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": task_ids[0],
                "changes": {"estimated_minutes": 20},
                "reason": "缩短焦点任务",
            },
            {
                "operation_type": "set_my_day",
                "task_id": task_ids[1],
                "is_in_my_day": False,
                "reason": "保持非焦点状态",
            },
        ],
        focus=[UUID(task_ids[0])],
        focus_minutes=999,
        buffer=0,
    )
    normalized = normalize_execution_refine_proposal(
        proposal=proposal,
        request=request,
        scope=scope,
    )
    assert normalized.buffer_minutes == 7
    assert normalized.estimated_focus_minutes == 20
    assert not any(
        isinstance(operation, SetMyDayOperation)
        and str(operation.task_id) == task_ids[1]
        for operation in normalized.operations
    )
    assert any(
        isinstance(operation, SetMyDayOperation)
        and str(operation.task_id) == task_ids[0]
        and operation.is_in_my_day
        for operation in normalized.operations
    )


def test_normalizer_final_capacity_fallback_drops_oversized_focus_without_rewriting_task():
    request = _request(mode="time_budget", available_minutes=10)
    scope = _scope(request=request)
    first_id = next(iter(scope.task_records))
    proposal = _valid_proposal(
        scope,
        request,
        operations=[
            {
                "operation_type": "update_task",
                "task_id": first_id,
                "changes": {"estimated_minutes": 10},
                "reason": "缩小任务",
            },
            {
                "operation_type": "set_my_day",
                "task_id": first_id,
                "is_in_my_day": True,
                "reason": "加入焦点",
            },
        ],
        focus=[UUID(first_id)],
        focus_minutes=10,
        buffer=3,
    )
    normalized = normalize_execution_refine_proposal(
        proposal=proposal,
        request=request,
        scope=scope,
        enforce_capacity_fallback=True,
    )
    assert normalized.focus_task_ids == []
    assert normalized.estimated_focus_minutes == 0
    assert normalized.operations[0].changes.estimated_minutes == 10
    assert not any(
        isinstance(operation, SetMyDayOperation) and operation.is_in_my_day
        for operation in normalized.operations
    )
    assert any("超出" in warning for warning in normalized.warnings)


def test_validator_does_not_charge_preexisting_exploration_errors_to_diff():
    planning_context = _planning_context()
    planning_context["intent_type"] = "exploration_decision"
    planning_context["time_horizon"] = "weeks"
    thread = _thread(task_tree=_task_tree(planning_context=planning_context))
    tasks = [
        _task(
            user_id=thread.user_id,
            client_node_id="task-1",
            phase_id="phase-1",
            sort_order=0,
        ),
        _task(
            user_id=thread.user_id,
            client_node_id="task-2",
            phase_id="phase-1",
            sort_order=1,
        ),
    ]
    request = _request()
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=tasks,
        dependencies=[],
        phase_reviews=[],
        request=request,
    )
    proposal = _valid_proposal(scope, request)
    codes = {
        issue.error_code
        for issue in validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        )
    }
    assert "EXECUTION_REFINE_DECISION_CONTEXT_MISSING" not in codes


def _apply_models(*, planning=False):
    tree = _task_tree(
        planning_context=_planning_context() if planning else None,
    )
    thread = _thread(task_tree=tree)
    tasks = [
        _task(
            user_id=thread.user_id,
            client_node_id="task-1",
            phase_id="phase-1" if planning else None,
            sort_order=0,
        ),
        _task(
            user_id=thread.user_id,
            client_node_id="task-2",
            phase_id="phase-1" if planning else None,
            sort_order=1,
        ),
    ]
    request = _request()
    scope = build_execution_refine_scope(
        thread=thread,
        tasks=tasks,
        dependencies=[],
        phase_reviews=[],
        request=request,
    )
    run = _run(thread, request, scope, status="ready")
    return thread, tasks, request, scope, run


def test_apply_atomically_updates_task_row_tree_and_is_idempotent():
    async def scenario():
        thread, tasks, request, scope, run = _apply_models()
        first = tasks[0]
        completed = _task(
            user_id=thread.user_id,
            client_node_id="history-completed",
            status="completed",
            sort_order=2,
        )
        assist = _task(
            user_id=thread.user_id,
            client_node_id="assist-child",
            source="task_assist",
            parent_task_id=first.id,
            sort_order=0,
        )
        all_tasks = [*tasks, completed, assist]
        scope = build_execution_refine_scope(
            thread=thread,
            tasks=all_tasks,
            dependencies=[],
            phase_reviews=[],
            request=request,
        )
        run = _run(thread, request, scope, status="ready")
        protected_before = {
            value.id: copy.deepcopy(
                {
                    key: item
                    for key, item in vars(value).items()
                    if key != "_sa_instance_state"
                }
            )
            for value in (completed, assist)
        }
        proposal = _valid_proposal(
            scope,
            request,
            operations=[
                {
                    "operation_type": "update_task",
                    "task_id": str(first.id),
                    "changes": {
                        "title": "写出演示稿的四条可验证结论",
                        "estimated_minutes": 25,
                        "done_criteria": "文档中保存四条带数据来源的结论",
                    },
                    "reason": "收紧当前交付动作",
                }
            ],
        )
        run.proposal = proposal.model_dump(mode="json", exclude_unset=True)
        session = FakeSession(
            [
                FakeResult(scalar=thread),
                FakeResult(scalar=run),
                FakeResult(scalars=all_tasks),
                FakeResult(scalars=[]),
                FakeResult(scalars=[]),
                FakeResult(scalar=thread),
            ]
        )
        receipt = await ExecutionRefineService(session).apply(
            user_id=thread.user_id,
            thread_id=thread.thread_id,
            request_id=request.request_id,
            expected_scope_fingerprint=scope.fingerprint,
        )
        tree = TaskTree.model_validate(thread.task_tree)
        tree_first = next(
            node for node in tree.root.children if node.client_node_id == "task-1"
        )
        assert first.title == tree_first.title == "写出演示稿的四条可验证结论"
        assert first.estimated_minutes == tree_first.estimated_minutes == 25
        assert run.status == "applied"
        assert receipt.affected_task_ids == [first.id]
        for value in (completed, assist):
            current = {
                key: item
                for key, item in vars(value).items()
                if key != "_sa_instance_state"
            }
            assert current == protected_before[value.id]

        repeated_session = FakeSession(
            [FakeResult(scalar=thread), FakeResult(scalar=run)]
        )
        repeated = await ExecutionRefineService(repeated_session).apply(
            user_id=thread.user_id,
            thread_id=thread.thread_id,
            request_id=request.request_id,
        )
        assert repeated == receipt
        assert repeated_session.added == []

    asyncio.run(scenario())


def test_apply_adds_deterministic_task_dependency_and_updates_phase_progress():
    async def scenario():
        thread, tasks, request, scope, run = _apply_models(planning=True)
        proposal = _valid_proposal(
            scope,
            request,
            operations=[
                {
                    "operation_type": "add_task",
                    "draft_id": "recovery-check",
                    "parent_task_id": None,
                    "title": "记录三条演示稿复核结论",
                    "description": "复核当前演示稿并记录问题",
                    "estimated_minutes": 15,
                    "done_criteria": "文档中保存三条带页码的复核结论",
                    "start_hint": "打开当前演示稿并定位第一页",
                    "fallback_action": None,
                    "depends_on_refs": [str(tasks[0].id)],
                    "insert_after_task_id": str(tasks[0].id),
                    "reason": "补齐当前阶段的复核动作",
                }
            ],
        )
        assert validate_execution_refine_proposal(
            proposal=proposal,
            request=request,
            scope=scope,
        ) == []
        run.proposal = proposal.model_dump(mode="json", exclude_unset=True)
        session = FakeSession(
            [
                FakeResult(scalar=thread),
                FakeResult(scalar=run),
                FakeResult(scalars=tasks),
                FakeResult(scalars=[]),
                FakeResult(scalars=[]),
                FakeResult(scalar=thread),
                FakeResult(scalars=tasks),
                FakeResult(scalars=[]),
            ]
        )
        receipt = await ExecutionRefineService(session).apply(
            user_id=thread.user_id,
            thread_id=thread.thread_id,
            request_id=request.request_id,
        )
        created = next(value for value in session.added if isinstance(value, Task))
        dependency = next(
            value for value in session.added if isinstance(value, TaskDependency)
        )
        assert created.client_node_id == execution_refine_client_node_id(
            request.request_id,
            "recovery-check",
        )
        assert created.metadata_["created_by"] == "execution_refine"
        assert created.metadata_["phase_id"] == "phase-1"
        assert dependency.task_id == created.id
        assert dependency.depends_on_task_id == tasks[0].id
        assert receipt.created_task_ids == [created.id]
        tree = TaskTree.model_validate(thread.task_tree)
        assert created.client_node_id in {
            node.client_node_id for node in tree.root.children
        }
        progress = calculate_phase_progress([*tasks, created], "phase-1")
        assert progress.total_ai_actions == 3
        assert tree.planning_context.next_action_client_node_id == "task-1"

    asyncio.run(scenario())


def test_apply_rejects_stale_scope_before_any_proposal_mutation():
    async def scenario():
        thread, tasks, request, scope, run = _apply_models()
        proposal = _valid_proposal(scope, request)
        run.proposal = proposal.model_dump(mode="json", exclude_unset=True)
        tasks[0].title = "用户刚刚改过的标题"
        before_tree = copy.deepcopy(thread.task_tree)
        before_minutes = tasks[0].estimated_minutes
        session = FakeSession(
            [
                FakeResult(scalar=thread),
                FakeResult(scalar=run),
                FakeResult(scalars=tasks),
                FakeResult(scalars=[]),
                FakeResult(scalars=[]),
            ]
        )
        with pytest.raises(ExecutionRefineError) as error:
            await ExecutionRefineService(session).apply(
                user_id=thread.user_id,
                thread_id=thread.thread_id,
                request_id=request.request_id,
            )
        assert error.value.code == "EXECUTION_REFINE_CONTEXT_STALE"
        assert tasks[0].estimated_minutes == before_minutes
        assert thread.task_tree == before_tree
        assert run.status == "ready"

    asyncio.run(scenario())


def test_apply_rolls_back_task_tree_task_rows_and_receipt_on_failure(monkeypatch):
    async def scenario():
        thread, tasks, request, scope, run = _apply_models()
        proposal = _valid_proposal(scope, request)
        run.proposal = proposal.model_dump(mode="json", exclude_unset=True)
        before_tree = copy.deepcopy(thread.task_tree)
        before_minutes = tasks[0].estimated_minutes
        session = RollbackFakeSession(
            [
                FakeResult(scalar=thread),
                FakeResult(scalar=run),
                FakeResult(scalars=tasks),
                FakeResult(scalars=[]),
                FakeResult(scalars=[]),
            ],
            tracked=[thread, run, *tasks],
        )

        async def fail_recalculation(*_args, **_kwargs):
            raise RuntimeError("simulated phase persistence failure")

        monkeypatch.setattr(
            "app.services.execution_refine.TaskRepository._recalculate_thread_phase_state",
            fail_recalculation,
        )
        with pytest.raises(RuntimeError, match="simulated phase persistence failure"):
            await ExecutionRefineService(session).apply(
                user_id=thread.user_id,
                thread_id=thread.thread_id,
                request_id=request.request_id,
            )
        assert tasks[0].estimated_minutes == before_minutes
        assert thread.task_tree == before_tree
        assert run.status == "ready"
        assert run.apply_receipt is None

    asyncio.run(scenario())


class SequenceProposalClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    async def create_execution_refine_proposal(self, *, prompt):
        self.prompts.append(prompt)
        value = self.payloads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_service_repairs_only_invalid_operations_and_stops_after_valid_proposal():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        task_ids = list(scope.task_records)
        invalid = _valid_proposal(
            scope,
            request,
            operations=[
                {
                    "operation_type": "reorder_siblings",
                    "parent_task_id": None,
                    "ordered_task_ids": [task_ids[0]],
                    "reason": "遗漏 sibling",
                }
            ],
        ).model_dump(mode="json", exclude_unset=True)
        valid = _valid_proposal(scope, request).model_dump(
            mode="json",
            exclude_unset=True,
        )
        client = SequenceProposalClient([invalid, valid])
        service = ExecutionRefineService(FakeSession([]), proposal_client=client)
        proposal = await service.generate_proposal(request=request, scope=scope)
        assert proposal.mode == request.mode
        assert len(client.prompts) == 2
        assert "EXECUTION_REFINE_SIBLING_SET_INVALID" in client.prompts[1]
        assert str(request.request_id) in client.prompts[1]

    asyncio.run(scenario())


def test_service_retries_transient_structured_output_failure_within_bound():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        valid = _valid_proposal(scope, request).model_dump(
            mode="json",
            exclude_unset=True,
        )
        client = SequenceProposalClient(
            [LLMStructuredOutputError("invalid provider json"), valid]
        )
        proposal = await ExecutionRefineService(
            FakeSession([]),
            proposal_client=client,
        ).generate_proposal(request=request, scope=scope)
        assert proposal.mode == request.mode
        assert len(client.prompts) == 2
        assert "EXECUTION_REFINE_SCHEMA_INVALID" in client.prompts[1]

    asyncio.run(scenario())


def test_service_limits_deterministic_repairs_to_two():
    async def scenario():
        request = _request()
        scope = _scope(request=request)
        task_ids = list(scope.task_records)
        invalid = _valid_proposal(
            scope,
            request,
            operations=[
                {
                    "operation_type": "reorder_siblings",
                    "parent_task_id": None,
                    "ordered_task_ids": [task_ids[0]],
                    "reason": "遗漏 sibling",
                }
            ],
        ).model_dump(mode="json", exclude_unset=True)
        client = SequenceProposalClient([invalid, invalid, invalid])
        service = ExecutionRefineService(FakeSession([]), proposal_client=client)
        with pytest.raises(ExecutionRefineError) as exc_info:
            await service.generate_proposal(request=request, scope=scope)
        assert exc_info.value.code == "EXECUTION_REFINE_INVALID_PROPOSAL"
        assert len(client.prompts) == 3

    asyncio.run(scenario())


def test_execution_refine_client_node_id_is_deterministic_and_request_scoped():
    request_id = uuid4()
    assert execution_refine_client_node_id(request_id, "draft-1") == execution_refine_client_node_id(
        request_id,
        "draft-1",
    )
    assert execution_refine_client_node_id(request_id, "draft-1") != execution_refine_client_node_id(
        uuid4(),
        "draft-1",
    )
