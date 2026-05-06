from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.thread import AgentThread


class AgentThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_thread(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        intent_text: str,
        selected_provider: str,
    ) -> AgentThread:
        thread = AgentThread(
            user_id=user_id,
            thread_id=thread_id,
            intent_text=intent_text,
            status="running",
            current_node="planner",
            next_nodes=[],
            interrupt_payload=None,
            latest_checkpoint_id=None,
            task_tree=None,
            error_code=None,
            error_message=None,
            expires_at=None,
            interrupted_at=None,
            completed_at=None,
        )
        self.session.add(thread)
        await self.session.commit()
        await self.session.refresh(thread)
        return thread

    async def get_thread_for_user(
        self,
        *,
        user_id: UUID,
        thread_id: str,
    ) -> AgentThread | None:
        result = await self.session.execute(
            select(AgentThread).where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
        )
        return result.scalar_one_or_none()

    async def mark_confirmation_accepted(self, *, thread: AgentThread, request_id: str) -> None:
        thread.status = "running"
        thread.updated_at = datetime.now(timezone.utc)
        await self.session.commit()


def thread_to_snapshot_payload(thread: AgentThread) -> dict[str, Any]:
    return {
        "thread_id": thread.thread_id,
        "status": thread.status,
        "state_version": 0,
        "last_event_id": None,
        "server_time": datetime.now(timezone.utc),
        "intent_text": thread.intent_text,
        "task_tree": thread.task_tree,
        "interrupt_payload": thread.interrupt_payload,
        "latest_checkpoint_id": thread.latest_checkpoint_id,
    }
