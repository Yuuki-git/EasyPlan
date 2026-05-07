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
    assert "/api/threads/{thread_id}/events" in schema["paths"]
    assert "get" in schema["paths"]["/api/threads/{thread_id}/events"]
    assert "/api/threads/{thread_id}/confirm" in schema["paths"]
    assert "post" in schema["paths"]["/api/threads/{thread_id}/confirm"]
    assert "/api/integrations/{provider}/oauth/start" in schema["paths"]
    assert "get" in schema["paths"]["/api/integrations/{provider}/oauth/start"]
    assert "/api/integrations/{provider}/oauth/callback" in schema["paths"]
    assert "get" in schema["paths"]["/api/integrations/{provider}/oauth/callback"]


def test_openapi_contract_documents_microsoft_todo_as_supported_provider():
    app = create_app()

    schema = app.openapi()

    assert "microsoft_todo" in schema["info"]["description"]


def test_openapi_contract_requires_timezone_on_mutating_planner_calls():
    app = create_app()

    schema = app.openapi()

    intent_params = _parameter_names(schema["paths"]["/api/intents"]["post"])
    confirm_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/confirm"]["post"])

    assert "X-User-Timezone" in intent_params
    assert "X-User-Timezone" in confirm_params


def test_openapi_contract_requires_authorization_on_thread_workflow():
    app = create_app()

    schema = app.openapi()

    intent_params = _parameter_names(schema["paths"]["/api/intents"]["post"])
    snapshot_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}"]["get"])
    events_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/events"]["get"])
    confirm_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/confirm"]["post"])

    assert "Authorization" in intent_params
    assert "Authorization" in snapshot_params
    assert "Authorization" in events_params
    assert "Authorization" in confirm_params


def test_openapi_contract_documents_sse_token_query_fallback():
    app = create_app()

    schema = app.openapi()

    events_params = _parameter_names(schema["paths"]["/api/threads/{thread_id}/events"]["get"])

    assert "token" in events_params


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
    assert properties["planner_provider"]["enum"] == ["openai", "deepseek", "xiaomi"]
    assert "planner_model" in properties
