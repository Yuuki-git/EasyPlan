from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.api.schemas import TaskTree
from app.services.strategy_context import (
    expected_strategy_type,
    strategy_context_enabled,
    validate_strategy_context,
)


def _action(client_node_id: str, title: str, minutes: int) -> dict:
    return {
        "client_node_id": client_node_id,
        "title": title,
        "description": None,
        "verb": title.split(" ", 1)[0],
        "estimated_minutes": minutes,
        "node_type": "action",
        "depends_on": [],
        "children": [],
        "done_criteria": f"{title} has a reviewable result",
    }


def _base_tree() -> dict:
    return {
        "root": {
            "client_node_id": "root",
            "title": "Plan",
            "description": None,
            "verb": "Plan",
            "estimated_minutes": 50,
            "node_type": "group",
            "depends_on": [],
            "children": [
                _action("draft", "Draft report", 30),
                _action("review", "Review report", 20),
            ],
        },
        "summary": "Create a reviewable report.",
        "assumptions": [],
        "planning_context": None,
    }


def _delivery_tree() -> dict:
    tree = _base_tree()
    tree["strategy_context"] = {
        "schema_version": 1,
        "strategy_type": "delivery",
        "deliverable": {
            "title": "Report draft",
            "format": "PDF report",
            "quality_bar": ["Contains findings and recommendation"],
        },
        "deadline": {"text": "tomorrow at noon", "is_explicit": True},
        "time_plan": {
            "available_minutes": 60,
            "planned_minutes": 50,
            "buffer_minutes": 10,
        },
        "scope": {
            "must_have": ["Findings"],
            "should_have": ["Appendix"],
            "can_cut": ["Visual polish"],
        },
        "workstreams": [
            {
                "workstream_id": "report",
                "title": "Report",
                "output": "Reviewable PDF",
                "task_client_node_ids": ["draft", "review"],
            }
        ],
        "critical_path_client_node_ids": ["draft", "review"],
    }
    return tree


def _decision_tree() -> dict:
    tree = _base_tree()
    tree["summary"] = (
        "It is worth continuing low-cost exploration, but do not decide yet. "
        "Compare role evidence and review after the experiment."
    )
    tree["planning_context"] = {
        "schema_version": 1,
        "intent_type": "exploration_decision",
        "time_horizon": "days",
        "roadmap": [
            {
                "phase_id": "clarify",
                "order": 1,
                "title": "Clarify",
                "objective": "Collect decision evidence",
                "status": "current",
            },
            {
                "phase_id": "test",
                "order": 2,
                "title": "Test",
                "objective": "Run a low-cost experiment",
                "status": "planned",
            },
            {
                "phase_id": "review",
                "order": 3,
                "title": "Review",
                "objective": "Make the user's decision explicit",
                "status": "planned",
            },
        ],
        "current_phase": {
            "phase_id": "clarify",
            "title": "Clarify",
            "objective": "Collect decision evidence",
        },
        "next_action_client_node_id": "draft",
    }
    tree["strategy_context"] = {
        "schema_version": 1,
        "strategy_type": "decision",
        "question": "Should I change careers?",
        "options": ["Continue exploring", "Stay in the current role"],
        "current_judgment": {
            "direction": "continue_exploring",
            "statement": "It is worth continuing low-cost exploration, but do not decide yet.",
            "confidence": "medium",
        },
        "basis": [
            {
                "statement": "The user has interest but no direct role experience.",
                "basis_type": "user_context",
            }
        ],
        "missing_information": ["Direct experience with the target role"],
        "experiments": [
            {
                "experiment_id": "role_test",
                "title": "Test the role",
                "hypothesis": "The daily work remains appealing after a small trial.",
                "success_signal": "Can name liked and disliked work activities.",
                "effort_level": "low",
                "task_client_node_ids": ["draft", "review"],
            }
        ],
        "decision_gate": {
            "review_after": "After the role test",
            "proceed_if": ["The core work remains appealing"],
            "stop_if": ["The actual work is not appealing"],
        },
    }
    return tree


def _codes(errors) -> set[str]:
    return {error.code for error in errors}


def test_schema_accepts_legacy_tree_without_strategy_context() -> None:
    parsed = TaskTree.model_validate(_base_tree())

    assert parsed.strategy_context is None


@pytest.mark.parametrize("factory", [_delivery_tree, _decision_tree])
def test_schema_accepts_each_strategy_context(factory) -> None:
    parsed = TaskTree.model_validate(factory())

    assert parsed.strategy_context is not None


def test_schema_rejects_unknown_strategy_type_and_extra_fields() -> None:
    unknown = _delivery_tree()
    unknown["strategy_context"]["strategy_type"] = "unknown"
    with pytest.raises(ValidationError):
        TaskTree.model_validate(unknown)

    extra = _delivery_tree()
    extra["strategy_context"]["unexpected"] = True
    with pytest.raises(ValidationError):
        TaskTree.model_validate(extra)


def test_intent_mapping_is_total_for_all_supported_intents() -> None:
    assert expected_strategy_type("long_term_growth") is None
    assert expected_strategy_type("short_term_delivery") == "delivery"
    assert expected_strategy_type("context_checklist") is None
    assert expected_strategy_type("exploration_decision") == "decision"
    assert expected_strategy_type("unknown") is None


def test_feature_flag_defaults_off_and_accepts_true(monkeypatch) -> None:
    monkeypatch.delenv("EASYPLAN_STRATEGY_CONTEXT_ENABLED", raising=False)
    assert strategy_context_enabled() is False

    monkeypatch.setenv("EASYPLAN_STRATEGY_CONTEXT_ENABLED", "true")
    assert strategy_context_enabled() is True


@pytest.mark.parametrize(
    ("intent_type", "tree", "expected_code"),
    [
        ("short_term_delivery", _base_tree, "DELIVERY_CONTEXT_MISSING"),
        ("exploration_decision", _base_tree, "DECISION_CONTEXT_MISSING"),
        ("long_term_growth", _delivery_tree, "STRATEGY_CONTEXT_FORBIDDEN"),
        ("context_checklist", _decision_tree, "STRATEGY_CONTEXT_FORBIDDEN"),
    ],
)
def test_validator_enforces_intent_context_matrix(intent_type, tree, expected_code) -> None:
    errors = validate_strategy_context(
        TaskTree.model_validate(tree()),
        intent_type=intent_type,
        enabled=True,
    )

    assert expected_code in _codes(errors)


def test_delivery_validator_collects_reference_time_and_budget_errors() -> None:
    tree = _delivery_tree()
    tree["root"]["children"].append(_action("draft", "Duplicate draft", 10))
    context = tree["strategy_context"]
    context["time_plan"] = {
        "available_minutes": 40,
        "planned_minutes": 45,
        "buffer_minutes": 0,
    }
    context["workstreams"][0]["task_client_node_ids"] = ["draft", "missing", "missing"]
    context["critical_path_client_node_ids"] = ["review", "missing"]

    errors = validate_strategy_context(
        TaskTree.model_validate(tree),
        intent_type="short_term_delivery",
        enabled=True,
    )
    codes = _codes(errors)

    assert "STRATEGY_TASK_ID_DUPLICATE" in codes
    assert "DELIVERY_PLANNED_MINUTES_MISMATCH" in codes
    assert "DELIVERY_TIME_BUDGET_EXCEEDED" in codes
    assert "DELIVERY_BUFFER_MISSING" in codes
    assert "DELIVERY_WORKSTREAM_REFERENCE_INVALID" in codes
    assert "DELIVERY_CRITICAL_PATH_MISSING" in codes


def test_delivery_validator_preserves_explicit_time_and_format_constraints() -> None:
    tree = TaskTree.model_validate(_delivery_tree())

    errors = validate_strategy_context(
        tree,
        intent_type="short_term_delivery",
        intent_text="I only have 2 hours to deliver a PPT by Friday at 5pm",
        enabled=True,
    )

    assert "DELIVERY_EXPLICIT_CONSTRAINT_DRIFT" in _codes(errors)


def test_delivery_validator_rejects_overplanned_single_email() -> None:
    errors = validate_strategy_context(
        TaskTree.model_validate(_delivery_tree()),
        intent_type="short_term_delivery",
        intent_text="今晚上线前发出一封客户道歉邮件",
        enabled=True,
    )

    assert "DELIVERY_SMALL_TASK_OVERPLANNED" in _codes(errors)


def test_decision_validator_collects_answer_reference_and_gate_errors() -> None:
    tree = _decision_tree()
    tree["summary"] = "Collect more information."
    context = tree["strategy_context"]
    context["current_judgment"] = {
        "direction": "continue_exploring",
        "statement": "More information is definitely needed.",
        "confidence": "high",
    }
    context["experiments"][0]["task_client_node_ids"] = ["missing", "missing"]
    context["basis"][0] = {
        "statement": "The target role will definitely be a good fit.",
        "basis_type": "working_assumption",
    }

    errors = validate_strategy_context(
        TaskTree.model_validate(tree),
        intent_type="exploration_decision",
        enabled=True,
    )
    codes = _codes(errors)

    assert "DECISION_ANSWER_MISSING" in codes
    assert "DECISION_OVERCONFIDENT" in codes
    assert "DECISION_EXPERIMENT_REFERENCE_INVALID" in codes
    assert "DECISION_ASSUMPTION_UNLABELED" in codes


def test_disabled_validator_preserves_legacy_behavior() -> None:
    errors = validate_strategy_context(
        TaskTree.model_validate(_base_tree()),
        intent_type="short_term_delivery",
        enabled=False,
    )

    assert errors == []
