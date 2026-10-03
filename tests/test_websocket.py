"""Tests for WebSocket real-time live events."""

from fastapi.testclient import TestClient
from app.main import create_app
from app.websocket_manager import ws_manager


def test_websocket_connection_and_initial_state():
    """Verify WebSocket client connects to /ws and receives INITIAL_STATE."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "INITIAL_STATE"
            assert "data" in data
            assert "stats" in data["data"]
            assert "total_files" in data["data"]["stats"]

            # Test ping/pong
            websocket.send_text("ping")
            resp = websocket.receive_text()
            assert resp == "pong"


def test_websocket_broadcast_sync():
    """Verify ws_manager.broadcast_sync pushes messages to connected client."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            init_msg = websocket.receive_json()
            assert init_msg["type"] == "INITIAL_STATE"

            # Broadcast a custom scan completed event
            ws_manager.broadcast_sync({
                "type": "SCAN_COMPLETED",
                "scan_id": 999,
                "data": {"test": "payload"},
            })

            broadcast_msg = websocket.receive_json()
            assert broadcast_msg["type"] == "SCAN_COMPLETED"
            assert broadcast_msg["scan_id"] == 999


def test_websocket_all_get_apis():
    """Verify all GET API endpoints can be queried via WebSocket."""
    import json
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            init_msg = websocket.receive_json()
            assert init_msg["type"] == "INITIAL_STATE"

            # 1. get_dashboard
            websocket.send_text(json.dumps({"action": "get_dashboard"}))
            res = websocket.receive_json()
            assert res["type"] == "DASHBOARD_DATA"
            assert res["status"] == "success"
            assert "stats" in res["data"]

            # 2. get_files
            websocket.send_text(json.dumps({"action": "get_files", "params": {"limit": 10}}))
            res = websocket.receive_json()
            assert res["type"] == "FILES_DATA"
            assert res["status"] == "success"

            # 3. get_new_files
            websocket.send_text(json.dumps({"action": "get_new_files"}))
            res = websocket.receive_json()
            assert res["type"] == "NEW_FILES_DATA"

            # 4. get_previous_files
            websocket.send_text(json.dumps({"action": "get_previous_files"}))
            res = websocket.receive_json()
            assert res["type"] == "PREVIOUS_FILES_DATA"

            # 5. get_duplicates
            websocket.send_text(json.dumps({"action": "get_duplicates"}))
            res = websocket.receive_json()
            assert res["type"] == "DUPLICATES_DATA"

            # 6. get_health
            websocket.send_text(json.dumps({"action": "get_health"}))
            res = websocket.receive_json()
            assert res["type"] == "HEALTH_DATA"
            assert "nas_directory" in res["data"]


def test_websocket_aliases_and_web_view():
    """Verify WebSocket aliases (/api/ws, /api/v1/ws) and HTML explorer page."""
    app = create_app()
    with TestClient(app) as client:
        # Test /api/ws alias
        with client.websocket_connect("/api/ws") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "INITIAL_STATE"

        # Test /api/v1/ws alias
        with client.websocket_connect("/api/v1/ws") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "INITIAL_STATE"

        # Test HTML explorer page
        resp = client.get("/websocket-api")
        assert resp.status_code == 200
        assert "WebSocket Live API Explorer" in resp.text
        assert "Mostasim Mahmud Tasim" in resp.text


def test_websocket_http_get_fallbacks():
    """Verify HTTP GET /ws returns JSON for API clients and redirects for browsers."""
    app = create_app()
    with TestClient(app) as client:
        # 1. API client GET /ws (JSON response)
        resp_json = client.get("/ws", headers={"Accept": "application/json"})
        assert resp_json.status_code == 200
        data = resp_json.json()
        assert data["status"] == "online"
        assert "websocket_url" in data
        assert "how_to_connect" in data

        # 2. API client GET /api/ws and /api/v1/ws
        resp_api = client.get("/api/ws")
        assert resp_api.status_code == 200
        assert resp_api.json()["status"] == "online"

        resp_v1 = client.get("/api/v1/ws")
        assert resp_v1.status_code == 200
        assert resp_v1.json()["status"] == "online"

        # 3. Browser GET /ws (redirects to /websocket-api)
        resp_browser = client.get("/ws", headers={"Accept": "text/html"}, follow_redirects=False)
        assert resp_browser.status_code == 303
        assert resp_browser.headers["location"] == "/websocket-api"
