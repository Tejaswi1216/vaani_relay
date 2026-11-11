import json
import pytest
from starlette.testclient import TestClient
from standin.app import app, state

@pytest.fixture(autouse=True)
def reset_standin():
    # Reset stand-in before every test
    client = TestClient(app)
    client.post("/admin/reset")
    # Reset default params
    client.post("/admin/params", json={
        "limits": {"tpm": 8000, "rpm": 30, "rpd": 1000, "tpd": 200000},
        "failures": {"error_rate": 0.0, "timeout_rate": 0.0, "slow_success_rate": 0.0},
        "latency": {"queue_time_ms_min": 1, "queue_time_ms_max": 5, "warm_ttft_ms_mean": 10, "warm_ttft_ms_std": 2, "decode_tokens_per_sec": 5000}
    })
    yield
    client.post("/admin/reset")

def test_chat_completions_streaming():
    client = TestClient(app)
    req_body = {
        "model": "qwen/qwen3.8-27b",
        "stream": True,
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "What is Helium in the Moshi architecture?"}]
    }
    
    with client.stream("POST", "/v1/chat/completions", json=req_body) as response:
        assert response.status_code == 200
        # Check rate limit headers
        assert "x-ratelimit-limit-requests" in response.headers
        assert "x-ratelimit-remaining-requests" in response.headers
        assert "x-ratelimit-limit-tokens" in response.headers
        assert "x-ratelimit-remaining-tokens" in response.headers
        
        chunks = []
        for line in response.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                payload = json.loads(line[6:])
                chunks.append(payload)

        assert len(chunks) > 0
        full_text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        assert "helium" in full_text.lower() or "moshi" in full_text.lower()

def test_tpm_rate_limiting():
    client = TestClient(app)
    # Set TPM to very low
    client.post("/admin/params", json={"limits": {"tpm": 50, "rpm": 30, "rpd": 1000, "tpd": 200000}})
    
    # Request that exceeds 50 tokens
    req_body = {
        "model": "qwen/qwen3.8-27b",
        "stream": True,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "This is a long prompt " * 20}]
    }
    
    res = client.post("/v1/chat/completions", json=req_body)
    assert res.status_code == 429
    assert "retry-after" in res.headers
    data = res.json()
    assert "Rate limit reached" in data["error"]["message"]
    assert data["error"]["type"] == "tokens"

def test_rpm_rate_limiting():
    client = TestClient(app)
    # Set RPM limit to 2
    client.post("/admin/params", json={"limits": {"tpm": 8000, "rpm": 1, "rpd": 1000, "tpd": 200000}})
    
    req_body = {
        "model": "qwen/qwen3.8-27b",
        "stream": True,
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "Hi"}]
    }
    
    # 1st call consumes the 1 RPM
    r1 = client.post("/v1/chat/completions", json=req_body)
    assert r1.status_code == 200
    
    # 2nd call hits 429
    r2 = client.post("/v1/chat/completions", json=req_body)
    assert r2.status_code == 429
    assert "retry-after" in r2.headers
    assert r2.json()["error"]["type"] == "requests"

def test_error_injection():
    client = TestClient(app)
    client.post("/admin/params", json={"failures": {"error_rate": 1.0}})
    
    req_body = {
        "model": "qwen/qwen3.8-27b",
        "stream": True,
        "messages": [{"role": "user", "content": "Hello"}]
    }
    res = client.post("/v1/chat/completions", json=req_body)
    assert res.status_code == 500
    assert res.json()["error"]["type"] == "server_error"

def test_admin_metrics():
    client = TestClient(app)
    res = client.get("/admin/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "in_flight" in data
    assert "total_requests" in data
    assert "remaining_tpm" in data
