import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult


class FakePipeline:
    def __init__(self):
        self.calls = set()

    def start_call(self):
        self.calls.add("abc")
        return "abc"

    def turn(self, call_id, text):
        if call_id not in self.calls:
            raise KeyError(call_id)
        return TurnResult(answer=f"응답:{text}", route="SHIPPING", confidence=0.9, action="ANSWER",
                          tools=[], guardrail={"ok": True, "violations": []}, elapsed_ms=5, end_call=False,
                          attempts=1)


class BrokenPipeline(FakePipeline):
    def turn(self, call_id, text):
        raise RuntimeError("LLM 호출 실패")


@pytest.fixture
def client(modumall_dir):
    return TestClient(create_app(FakePipeline(), load_domain(modumall_dir)))


def test_domain_endpoint(client):
    r = client.get("/api/domain")
    assert r.status_code == 200 and r.json()["name"] == "모두몰" and "greeting" in r.json()


def test_start_and_turn(client):
    s = client.post("/api/call/start").json()
    assert s["call_id"] == "abc" and s["greeting"]
    t = client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"}).json()
    assert t["answer"] == "응답:배송비" and t["action"] == "ANSWER" and t["end_call"] is False


def test_unknown_call_is_404(client):
    r = client.post("/api/call/turn", json={"call_id": "zzz", "text": "x"})
    assert r.status_code == 404


def test_empty_text_is_422(client):
    r = client.post("/api/call/turn", json={"call_id": "abc", "text": "  "})
    assert r.status_code == 422


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_pipeline_error_is_500_with_message(modumall_dir):
    broken_client = TestClient(create_app(BrokenPipeline(), load_domain(modumall_dir)), raise_server_exceptions=False)
    broken_client.post("/api/call/start")
    r = broken_client.post("/api/call/turn", json={"call_id": "abc", "text": "배송비"})
    assert r.status_code == 500 and "LLM 호출 실패" in r.json()["detail"]
