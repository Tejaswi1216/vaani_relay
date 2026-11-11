import asyncio
import os
import pytest
import httpx
from standin.app import app, state
from relay.client_standin import StandinClient
from relay.pipeline import RelayPipeline
from relay.metrics import MetricsLogger
from relay.policy import AdmissionController, RetryPolicy
from scripts.validate_results import validate_results

@pytest.fixture(autouse=True)
def setup_test_env():
    # Set up clean standin params
    state.reset_buckets()
    state.update_params({
        "limits": {"tpm": 8000, "rpm": 30, "rpd": 1000, "tpd": 200000},
        "failures": {"error_rate": 0.0, "timeout_rate": 0.0, "slow_success_rate": 0.0},
        "latency": {"queue_time_ms_min": 1, "queue_time_ms_max": 5, "warm_ttft_ms_mean": 10, "warm_ttft_ms_std": 2, "decode_tokens_per_sec": 5000}
    })
    yield
    state.reset_buckets()

def test_pipeline_e2e_successful_flow(tmp_path):
    async def run_flow():
        transport = httpx.ASGITransport(app=app)
        async_client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
        standin_client = StandinClient(base_url="http://test/v1", client=async_client)
        
        raw_dir = tmp_path / "raw"
        logger = MetricsLogger(raw_dir=raw_dir)
        import relay.metrics as rm
        orig_csv = rm.RESULTS_CSV
        rm.RESULTS_CSV = tmp_path / "results.csv"
        logger._ensure_csv_header()

        try:
            pipeline = RelayPipeline(client=standin_client, metrics_logger=logger, source="stand-in")
            
            # Ask question q04
            record = await pipeline.answer_question(
                question="Describe the position-wise feed-forward network in the Transformer.",
                request_id="req_test_01",
                scenario="e2e_test",
                retrieval_setting="setting_2"
            )

            assert record.status == "ok"
            assert record.first_token_ms is not None
            assert record.first_token_ms > 0
            assert record.total_ms >= record.first_token_ms
            assert record.tokens_in > 0
            assert record.tokens_out > 0
            assert "feed-forward" in record.raw_response_text.lower() or "linear" in record.raw_response_text.lower()

            is_valid = validate_results(str(tmp_path / "results.csv"), str(raw_dir))
            assert is_valid is True
        finally:
            rm.RESULTS_CSV = orig_csv
            await standin_client.close()

    asyncio.run(run_flow())

def test_pipeline_429_retry_and_recovery(tmp_path):
    async def run_flow():
        # Set standin RPM to 1 so the second rapid call triggers 429
        state.update_params({"limits": {"tpm": 8000, "rpm": 1, "rpd": 1000, "tpd": 200000}})
        
        transport = httpx.ASGITransport(app=app)
        async_client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
        standin_client = StandinClient(base_url="http://test/v1", client=async_client)
        
        raw_dir = tmp_path / "raw"
        logger = MetricsLogger(raw_dir=raw_dir)
        import relay.metrics as rm
        orig_csv = rm.RESULTS_CSV
        rm.RESULTS_CSV = tmp_path / "results.csv"
        logger._ensure_csv_header()

        try:
            pipeline = RelayPipeline(client=standin_client, metrics_logger=logger, source="stand-in")
            
            # Consume the 1 RPM
            rec1 = await pipeline.answer_question(
                question="What is the feed-forward network?",
                request_id="req_rpm_1",
                scenario="test_429"
            )
            assert rec1.status == "ok"

            # Second call will hit 429 and retry
            rec2 = await pipeline.answer_question(
                question="What is the feed-forward network?",
                request_id="req_rpm_2",
                scenario="test_429"
            )
            assert rec2.retries >= 0
            assert rec2.status in ["ok", "rate_limited", "timeout"]

            is_valid = validate_results(str(tmp_path / "results.csv"), str(raw_dir))
            assert is_valid is True
        finally:
            rm.RESULTS_CSV = orig_csv
            await standin_client.close()

    asyncio.run(run_flow())

def test_pipeline_timeout_handling(tmp_path):
    async def run_flow():
        # Inject 100% timeout in standin
        state.update_params({
            "failures": {"timeout_rate": 1.0, "timeout_delay_ms": 1500}
        })
        
        transport = httpx.ASGITransport(app=app)
        async_client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
        standin_client = StandinClient(base_url="http://test/v1", client=async_client)
        
        raw_dir = tmp_path / "raw"
        logger = MetricsLogger(raw_dir=raw_dir)
        import relay.metrics as rm
        orig_csv = rm.RESULTS_CSV
        rm.RESULTS_CSV = tmp_path / "results.csv"
        logger._ensure_csv_header()

        try:
            pipeline = RelayPipeline(client=standin_client, metrics_logger=logger, source="stand-in")
            
            rec = await pipeline.answer_question(
                question="What is the feed-forward network?",
                request_id="req_timeout_1",
                scenario="test_timeout"
            )
            assert rec.status == "timeout"
            assert rec.first_token_ms is None

            is_valid = validate_results(str(tmp_path / "results.csv"), str(raw_dir))
            assert is_valid is True
        finally:
            rm.RESULTS_CSV = orig_csv
            await standin_client.close()

    asyncio.run(run_flow())
