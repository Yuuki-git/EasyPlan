from app.main import create_app


def _parameter_names(operation: dict) -> set[str]:
    return {parameter["name"] for parameter in operation.get("parameters", [])}


def test_openapi_contract_exposes_backend_protocol_endpoints():
    app = create_app()

    schema = app.openapi()

    assert "/api/intents" in schema["paths"]
    assert "post" in schema["paths"]["/api/intents"]
    assert "/api/threads/{thread_id}" in schema["paths"]
    assert "get" in schema["paths"]["/api/threads/{thread_id}"]
    assert "delete" in schema["paths"]["/api/threads/{thread_id}"]
    assert "/api/threads/{thread_id}/events" in schema["paths"]
    assert "get" in schema["paths"]["/api/threads/{thread_id}/events"]
    assert "/api/threads/{thread_id}/confirm" in schema["paths"]
    assert "post" in schema["paths"]["/api/threads/{thread_id}/confirm"]
    assert "/api/threads/{thread_id}/phases/next" in schema["paths"]
    assert "post" in schema["paths"]["/api/threads/{thread_id}/phases/next"]
    assert "/api/threads/{thread_id}/phases/next/cancel" in schema["paths"]
    assert "delete" in schema["paths"]["/api/threads/{thread_id}/phases/next/cancel"]
    assert "/api/tasks" in schema["paths"]
    assert "get" in schema["paths"]["/api/tasks"]
    assert "post" in schema["paths"]["/api/tasks"]
    assert "/api/tasks/{task_id}" in schema["paths"]
    assert "patch" in schema["paths"]["/api/tasks/{task_id}"]
    assert "delete" in schema["paths"]["/api/tasks/{task_id}"]
    expected_paths = {
        "/api/threads/{thread_id}/practice-loops/{loop_id}/schedule-today": "post",
        "/api/threads/{thread_id}/phases/{phase_id}/review": "put",
        "/api/threads/{thread_id}/phases/{phase_id}/review/decision": "post",
    }
    for path, method in expected_paths.items():
        assert method in schema["paths"][path]


def test_openapi_contract_is_native_task_board_only():
    app = create_app()

    schema = app.openapi()

    assert all(not path.startswith("/api/integrations") for path in schema["paths"])
    assert "todoist" not in schema["info"]["description"].lower()
    assert "microsoft_todo" not in schema["info"]["description"].lower()
    assert "native task board" in schema["info"]["description"].lower()


def test_openapi_contract_requires_timezone_on_mutating_planner_calls():
    app = create_app()

    schema = app.openapi()

    intent_params = _parameter_names(schema["paths"]["/api/intents"]["post"])
    confirm_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/confirm"]["post"])
    next_phase_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/phases/next"]["post"])
    next_phase_cancel_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/phases/next/cancel"]["delete"])

    assert "X-User-Timezone" in intent_params
    assert "X-User-Timezone" in confirm_params
    assert "X-User-Timezone" in next_phase_params


def test_openapi_contract_requires_authorization_on_thread_workflow():
    app = create_app()

    schema = app.openapi()

    intent_params = _parameter_names(schema["paths"]["/api/intents"]["post"])
    snapshot_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}"]["get"])
    thread_delete_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}"]["delete"])
    events_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/events"]["get"])
    confirm_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/confirm"]["post"])
    next_phase_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/phases/next"]["post"])
    next_phase_cancel_params = _parameter_names(
        schema["paths"]["/api/threads/{thread_id}/phases/next/cancel"]["delete"]
    )
    tasks_params = _parameter_names(schema["paths"]["/api/tasks"]["get"])
    task_create_params = _parameter_names(schema["paths"]["/api/tasks"]["post"])
    task_patch_params = _parameter_names(schema["paths"]["/api/tasks/{task_id}"]["patch"])
    task_delete_params = _parameter_names(schema["paths"]["/api/tasks/{task_id}"]["delete"])

    assert "Authorization" in intent_params
    assert "Authorization" in snapshot_params
    assert "Authorization" in thread_delete_params
    assert "Authorization" in events_params
    assert "Authorization" in confirm_params
    assert "Authorization" in next_phase_params
    assert "Authorization" in next_phase_cancel_params
    assert "Authorization" in tasks_params
    assert "Authorization" in task_create_params
    assert "Authorization" in task_patch_params
    assert "Authorization" in task_delete_params


def test_openapi_contract_exposes_native_task_board_schemas():
    app = create_app()

    schema = app.openapi()

    task_response = schema["components"]["schemas"]["TaskResponse"]
    task_create = schema["components"]["schemas"]["TaskCreateRequest"]
    task_update = schema["components"]["schemas"]["TaskUpdateRequest"]
    task_properties = task_response["properties"]

    assert "view_bucket" in task_properties
    assert "is_in_my_day" in task_properties
    assert "estimated_minutes" in task_properties
    assert "parent_task_id" in task_properties
    assert "client_node_id" in task_properties
    assert "done_criteria" in task_properties
    assert "start_hint" in task_properties
    assert "fallback_action" in task_properties
    assert {"title", "description", "view_bucket", "is_in_my_day", "parent_task_id", "thread_id"}.issubset(
        task_create["properties"]
    )
    assert task_create["properties"]["view_bucket"]["default"] == "planned"
    assert task_create["properties"]["is_in_my_day"]["default"] is False
    assert "view_bucket" in task_update["properties"]
    assert "is_in_my_day" in task_update["properties"]


def test_openapi_contract_exposes_phase_planning_contract():
    schema = create_app().openapi()

    task_properties = schema["components"]["schemas"]["TaskResponse"]["properties"]
    tree_schema_name = next(
        name
        for name in ("TaskTree", "TaskTree-Output")
        if name in schema["components"]["schemas"]
    )
    tree_properties = schema["components"]["schemas"][tree_schema_name]["properties"]

    assert {"source", "phase_id", "phase_order"}.issubset(task_properties)
    assert "planning_context" in tree_properties
    assert "NextPhaseRequest" in schema["components"]["schemas"]
    assert "NextPhaseResponse" in schema["components"]["schemas"]
    cancel_operation = schema["paths"]["/api/threads/{thread_id}/phases/next/cancel"]["delete"]
    cancel_response = cancel_operation["responses"]["200"]
    request_id_parameter = next(
        parameter
        for parameter in cancel_operation["parameters"]
        if parameter["in"] == "query" and parameter["name"] == "request_id"
    )
    assert cancel_response["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ThreadSnapshot"
    assert request_id_parameter["required"] is True
    assert request_id_parameter["schema"]["minLength"] == 8
    assert request_id_parameter["schema"]["maxLength"] == 128


def test_openapi_contract_exposes_long_term_execution_snapshot_and_requests():
    schema = create_app().openapi()

    thread_snapshot = schema["components"]["schemas"]["ThreadSnapshot"]
    assert "long_term_execution" in thread_snapshot["properties"]
    assert "PracticeLoopProgressResponse" in schema["components"]["schemas"]
    assert "PhaseReviewResponse" in schema["components"]["schemas"]
    assert "LongTermExecutionSnapshot" in schema["components"]["schemas"]
    assert "PhaseReviewUpdateRequest" in schema["components"]["schemas"]
    assert "PhaseReviewDecisionRequest" in schema["components"]["schemas"]


def test_openapi_contract_requires_initial_run_request_id_in_intent_response():
    app = create_app()

    schema = app.openapi()

    intent_response = schema["components"]["schemas"]["IntentCreateResponse"]
    assert "request_id" in intent_response["properties"]
    assert "request_id" in intent_response["required"]
    assert intent_response["properties"]["request_id"]["format"] == "uuid"


def test_openapi_contract_documents_sse_token_query_fallback():
    app = create_app()

    schema = app.openapi()

    intent_params = _parameter_names(schema["paths"]["/api/intents"]["post"])
    snapshot_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}"]["get"])
    events_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/events"]["get"])
    confirm_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/confirm"]["post"])

    assert "token" in events_params
    assert "token" not in intent_params
    assert "token" not in snapshot_params
    assert "token" not in confirm_params


def test_openapi_contract_documents_sse_last_event_id_query_fallback():
    app = create_app()

    schema = app.openapi()

    event_parameters = schema["paths"]["/api/threads/{thread_id}/events"]["get"]["parameters"]
    events_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/events"]["get"])
    request_id_param = next(
        parameter
        for parameter in event_parameters
        if parameter["name"] == "request_id"
    )
    run_type_param = next(parameter for parameter in event_parameters if parameter["name"] == "run_type")

    assert "last_event_id" in events_params
    assert "run_type" in events_params
    assert "request_id" in events_params
    assert request_id_param["required"] is True
    assert run_type_param["schema"]["enum"] == ["initial", "next_phase", "refine"]


def test_openapi_contract_documents_sse_envelope_and_agent_error_event_name():
    app = create_app()

    schema = app.openapi()

    description = schema["paths"]["/api/threads/{thread_id}/events"]["get"]["description"]
    assert "agent_error" in description
    assert "still_running" in description
    assert "event_id" in description
    assert "seq" in description
    assert "payload" in description
    assert "reasoning" not in description
    assert "checkpoint" not in description
    assert " and error" not in description
    assert "The error event payload" not in description


def test_openapi_contract_confirm_action_includes_refine():
    app = create_app()

    schema = app.openapi()

    confirmation_action = schema["components"]["schemas"]["ConfirmationAction"]
    assert confirmation_action["enum"] == ["approve", "edit", "refine", "reject"]


def test_openapi_contract_thread_snapshot_supports_sse_state_alignment():
    app = create_app()

    schema = app.openapi()

    thread_snapshot = schema["components"]["schemas"]["ThreadSnapshot"]
    required = set(thread_snapshot["required"])
    assert {"state_version", "last_event_id", "server_time"}.issubset(required)


def test_openapi_contract_intent_supports_model_provider_selection():
    app = create_app()

    schema = app.openapi()

    properties = schema["components"]["schemas"]["IntentCreateRequest"]["properties"]
    assert properties["preferred_provider"]["default"] == "native"
    planner_provider_schema = properties["planner_provider"]
    variants = planner_provider_schema.get("anyOf", [planner_provider_schema])
    enum_variant = next(variant for variant in variants if "enum" in variant)

    assert enum_variant["enum"] == ["openai", "deepseek", "xiaomi"]
    assert any(variant.get("type") == "null" for variant in variants)
    assert planner_provider_schema.get("default") is None
    assert "planner_model" in properties
