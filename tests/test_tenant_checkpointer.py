import pytest

from app.services.checkpoint_service import (
    InMemoryTenantCheckpointStore,
    TenantAwareMemorySaver,
    TenantCheckpointRecord,
)


def test_checkpointer_reads_only_matching_user_and_thread():
    store = InMemoryTenantCheckpointStore()
    record = TenantCheckpointRecord(
        user_id="user_a",
        thread_id="thread_1",
        checkpoint_ns="",
        checkpoint_id="checkpoint_1",
        checkpoint={"state": "owned-by-a"},
        metadata={"node": "planner_node"},
    )

    store.put(record)

    assert store.get("user_a", "thread_1", "", "checkpoint_1") == record
    assert store.get("user_b", "thread_1", "", "checkpoint_1") is None


def test_checkpointer_allows_same_thread_id_across_tenants_without_leakage():
    store = InMemoryTenantCheckpointStore()
    store.put(
        TenantCheckpointRecord(
            user_id="user_a",
            thread_id="shared_thread",
            checkpoint_ns="",
            checkpoint_id="checkpoint_1",
            checkpoint={"owner": "a"},
        )
    )
    store.put(
        TenantCheckpointRecord(
            user_id="user_b",
            thread_id="shared_thread",
            checkpoint_ns="",
            checkpoint_id="checkpoint_1",
            checkpoint={"owner": "b"},
        )
    )

    assert store.get("user_a", "shared_thread", "", "checkpoint_1").checkpoint == {"owner": "a"}
    assert store.get("user_b", "shared_thread", "", "checkpoint_1").checkpoint == {"owner": "b"}


def test_checkpointer_lists_checkpoints_with_tenant_filter():
    store = InMemoryTenantCheckpointStore()
    store.put(
        TenantCheckpointRecord(
            user_id="user_a",
            thread_id="thread_1",
            checkpoint_ns="",
            checkpoint_id="checkpoint_1",
            checkpoint={"step": 1},
        )
    )
    store.put(
        TenantCheckpointRecord(
            user_id="user_b",
            thread_id="thread_1",
            checkpoint_ns="",
            checkpoint_id="checkpoint_2",
            checkpoint={"step": 2},
        )
    )

    records = store.list_for_thread("user_a", "thread_1")

    assert [record.checkpoint_id for record in records] == ["checkpoint_1"]


def test_langgraph_checkpointer_rejects_list_without_tenant_config():
    checkpointer = TenantAwareMemorySaver()

    with pytest.raises(ValueError, match="requires user_id and thread_id"):
        list(checkpointer.list(None))
