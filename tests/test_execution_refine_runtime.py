import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.schemas import ExecutionRefineProposal
from app.services.execution_refine_runtime import (
    ExecutionRefineRunKey,
    ExecutionRefineRuntime,
)


class SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *_args):
        return None


class LeaseRenewingRuntime(ExecutionRefineRuntime):
    async def _renew_durable_lease(self, **_kwargs):
        return True


def _run(*, status="running", request_id=None, proposal=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        thread_id="thread-1",
        request_id=request_id or uuid4(),
        mode="context_change",
        input_context={
            "request_id": str(request_id or uuid4()),
            "mode": "context_change",
            "user_context": "今天需要先处理演示稿",
        },
        scope_fingerprint="a" * 64,
        status=status,
        stage="queued" if status == "running" else status,
        proposal=proposal,
        apply_receipt=None,
        error_code=None,
        error_message=None,
        lease_owner=None,
        lease_expires_at=now + timedelta(minutes=1),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )


def _normalize_run(run):
    run.input_context["request_id"] = str(run.request_id)
    return run


def _proposal(task_id=None):
    task_id = task_id or uuid4()
    return ExecutionRefineProposal.model_validate(
        {
            "schema_version": 1,
            "proposal_type": "execution_refine",
            "mode": "context_change",
            "summary": "保留目标，仅调整今天的执行顺序。",
            "user_facing_reasons": ["先处理当前最重要的交付物。"],
            "preserved_constraints": ["不修改已完成任务和历史记录。"],
            "operations": [
                {
                    "operation_type": "set_my_day",
                    "task_id": str(task_id),
                    "is_in_my_day": True,
                    "reason": "今天优先完成。",
                }
            ],
            "focus_task_ids": [str(task_id)],
            "estimated_focus_minutes": 30,
            "buffer_minutes": 0,
            "warnings": [],
        }
    )


def _event_type(event):
    return next(line[7:] for line in event.splitlines() if line.startswith("event: "))


def _event_id(event):
    return next(line[4:] for line in event.splitlines() if line.startswith("id: "))


def _event_data(event):
    line = next(line[6:] for line in event.splitlines() if line.startswith("data: "))
    return json.loads(line)


async def _collect(runtime, run, last_event_id=None):
    events = []
    async for event in runtime.stream(
        thread_id=run.thread_id,
        request_id=run.request_id,
        last_event_id=last_event_id,
    ):
        events.append(event)
    return events


def _patch_runtime_dependencies(monkeypatch, *, run, proposal, release=None, started=None):
    async def get_owned(_repository, **_kwargs):
        return run

    async def claim_lease(_repository, target, *, lease_owner, **_kwargs):
        target.lease_owner = lease_owner
        return True

    async def mark_stage(_repository, target, stage, **_kwargs):
        target.stage = stage
        return True

    async def save_proposal(_repository, target, value, **_kwargs):
        target.proposal = value.model_dump(mode="json")
        target.status = "ready"
        target.stage = "ready"
        return True

    async def fail(_repository, target, *, code, message, **_kwargs):
        target.status = "failed"
        target.stage = "failed"
        target.error_code = code
        target.error_message = message
        return True

    async def load_scope(_service, **_kwargs):
        return SimpleNamespace(fingerprint=run.scope_fingerprint)

    async def generate(_service, *, on_stage, **_kwargs):
        if started is not None:
            started.set()
        if release is not None:
            await release.wait()
        await on_stage("validating", {"attempt": 1})
        return proposal

    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineRepository.get_owned",
        get_owned,
    )
    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineRepository.claim_lease",
        claim_lease,
    )
    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineRepository.mark_stage",
        mark_stage,
    )
    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineRepository.save_proposal",
        save_proposal,
    )
    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineRepository.fail",
        fail,
    )
    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineService.load_scope",
        load_scope,
    )
    monkeypatch.setattr(
        "app.services.execution_refine_runtime.ExecutionRefineService.generate_proposal",
        generate,
    )


def test_stage_is_visible_before_provider_completion(monkeypatch):
    async def scenario():
        run = _normalize_run(_run())
        started = asyncio.Event()
        release = asyncio.Event()
        _patch_runtime_dependencies(
            monkeypatch,
            run=run,
            proposal=_proposal(),
            release=release,
            started=started,
        )
        runtime = LeaseRenewingRuntime(
            session_factory=SessionContext,
            proposal_client_factory=lambda: object(),
            heartbeat_interval_seconds=60,
        )
        running = asyncio.create_task(
            runtime.run(
                user_id=run.user_id,
                thread_id=run.thread_id,
                request_id=run.request_id,
            )
        )
        await started.wait()
        key = ExecutionRefineRunKey(run.thread_id, str(run.request_id))
        assert [_event_type(event) for event in runtime._events[key]][:3] == [
            "run_started",
            "execution_context_ready",
            "refine_generation_started",
        ]
        release.set()
        await running

    asyncio.run(scenario())

def test_heartbeat_uses_same_run_identity(monkeypatch):
    async def scenario():
        run = _normalize_run(_run())
        started = asyncio.Event()
        release = asyncio.Event()
        _patch_runtime_dependencies(
            monkeypatch,
            run=run,
            proposal=_proposal(),
            release=release,
            started=started,
        )
        runtime = LeaseRenewingRuntime(
            session_factory=SessionContext,
            proposal_client_factory=lambda: object(),
            heartbeat_interval_seconds=0.01,
        )
        running = asyncio.create_task(
            runtime.run(
                user_id=run.user_id,
                thread_id=run.thread_id,
                request_id=run.request_id,
            )
        )
        await started.wait()
        await asyncio.sleep(0.025)
        release.set()
        await running
        key = ExecutionRefineRunKey(run.thread_id, str(run.request_id))
        heartbeat = next(
            event for event in runtime._events[key] if _event_type(event) == "still_running"
        )
        data = _event_data(heartbeat)
        assert data["run_type"] == "execution_refine"
        assert data["thread_id"] == run.thread_id
        assert data["request_id"] == str(run.request_id)

    asyncio.run(scenario())


def test_reconnect_cursor_never_replays_another_run_terminal_event():
    async def scenario():
        runtime = ExecutionRefineRuntime(session_factory=SessionContext)
        first = _normalize_run(_run())
        second = _normalize_run(_run(request_id=uuid4()))
        first_key = ExecutionRefineRunKey(first.thread_id, str(first.request_id))
        second_key = ExecutionRefineRunKey(second.thread_id, str(second.request_id))
        runtime._append(first_key, "done", {"status": "ready"})
        runtime._append(second_key, "run_started", {"stage": "queued"})

        stream = runtime.stream(
            thread_id=second.thread_id,
            request_id=second.request_id,
            last_event_id=_event_id(runtime._events[first_key][0]),
        )
        event = await anext(stream)
        await stream.aclose()
        assert _event_type(event) == "snapshot_required"
        assert str(first.request_id) not in event
        assert str(second.request_id) in event

    asyncio.run(scenario())


def test_process_restart_reconstructs_ready_and_error_snapshots():
    ready = _normalize_run(
        _run(status="ready", proposal=_proposal().model_dump(mode="json"))
    )
    failed = _normalize_run(_run(status="failed", request_id=uuid4()))
    failed.error_code = "EXECUTION_REFINE_INVALID_PROPOSAL"
    failed.error_message = "暂时无法生成可靠的调整方案。"
    runtime = ExecutionRefineRuntime(session_factory=SessionContext)

    runtime.restore_from_snapshot(ready)
    runtime.restore_from_snapshot(failed)

    ready_key = ExecutionRefineRunKey(ready.thread_id, str(ready.request_id))
    failed_key = ExecutionRefineRunKey(failed.thread_id, str(failed.request_id))
    assert [_event_type(event) for event in runtime._events[ready_key]] == [
        "diff_ready",
        "done",
    ]
    assert [_event_type(event) for event in runtime._events[failed_key]] == [
        "agent_error"
    ]


def test_cancellation_is_idempotent_and_wins_over_late_provider(monkeypatch):
    async def scenario():
        run = _normalize_run(_run())
        started = asyncio.Event()
        release = asyncio.Event()
        _patch_runtime_dependencies(
            monkeypatch,
            run=run,
            proposal=_proposal(),
            release=release,
            started=started,
        )
        runtime = LeaseRenewingRuntime(
            session_factory=SessionContext,
            proposal_client_factory=lambda: object(),
            heartbeat_interval_seconds=60,
        )
        running = asyncio.create_task(
            runtime.run(
                user_id=run.user_id,
                thread_id=run.thread_id,
                request_id=run.request_id,
            )
        )
        await started.wait()
        await runtime.cancel(thread_id=run.thread_id, request_id=run.request_id)
        await runtime.cancel(thread_id=run.thread_id, request_id=run.request_id)
        release.set()
        await running
        key = ExecutionRefineRunKey(run.thread_id, str(run.request_id))
        events = list(runtime._events[key])
        assert [_event_type(event) for event in events].count("done") == 1
        assert "diff_ready" not in {_event_type(event) for event in events}
        assert _event_data(events[-1])["payload"]["status"] == "cancelled"

    asyncio.run(scenario())
