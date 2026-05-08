import logging

from fastapi.testclient import TestClient

from app.main import create_app


def test_global_exception_handler_returns_sanitized_json_and_logs_traceback(caplog):
    app = create_app(enable_static=False)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("database statement leaked details")

    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error_code": "INTERNAL_ERROR",
        "message": "服务器在思考时走神了，请稍后再试。",
    }
    assert "RuntimeError" not in response.text
    assert "database statement leaked details" not in response.text
    assert "unhandled_exception" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "database statement leaked details" in caplog.text
