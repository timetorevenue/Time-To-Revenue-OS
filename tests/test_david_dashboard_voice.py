from fastapi.testclient import TestClient

from dashboard.backend import main
import david_speech


def test_local_dashboard_recording_returns_draft_and_denies_cross_origin(monkeypatch):
    client = TestClient(main.app, base_url="http://localhost")
    route = "/api/dashboard/ask-david/transcribe"
    requests = []

    def transcribe(data, suffix):
        requests.append((bytes(data), suffix))
        return "Editable draft.", {}

    monkeypatch.setattr(david_speech, "transcribe", transcribe)
    headers = {"Origin": "http://localhost:3010", "X-TTROS-Connections": "1", "Content-Type": "audio/webm"}
    assert client.post(route, headers={**headers, "Origin": "http://localhost:4001"}, content=b"clip").status_code == 403
    assert client.post(route, headers={**headers, "CF-Ray": "fixture"}, content=b"clip").status_code == 403
    assert not requests
    response = client.post(route, headers=headers, content=b"clip")
    assert response.status_code == 200 and response.json() == {"text": "Editable draft."}
    assert response.headers["Cache-Control"] == "no-store"
    assert requests == [(b"clip", ".webm")]
