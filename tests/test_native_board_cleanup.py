from pathlib import Path


def test_external_sync_and_oauth_modules_are_removed_for_native_board_pivot():
    project_root = Path(__file__).resolve().parents[1]

    removed_paths = [
        "app/api/routes_integrations.py",
        "app/services/mcp_adapters.py",
        "app/services/oauth_service.py",
        "app/services/sync_service.py",
        "app/models/integration.py",
        "app/models/sync.py",
    ]

    for relative_path in removed_paths:
        assert not (project_root / relative_path).exists()
