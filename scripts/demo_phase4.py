import asyncio
import httpx
from standin.app import app, state
from relay.client_standin import StandinClient
from relay.pipeline import RelayPipeline
from relay.metrics import MetricsLogger
from scripts.validate_results import validate_results

async def main():
    state.reset_buckets()
    transport = httpx.ASGITransport(app=app)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    standin_client = StandinClient(base_url="http://test/v1", client=async_client)
    logger = MetricsLogger()
    pipeline = RelayPipeline(client=standin_client, metrics_logger=logger, source="stand-in")
    
    rec = await pipeline.answer_question(
        question="What is Helium in the Moshi architecture, and what are its model size and pretraining scale?",
        request_id="req_demo_phase4_01",
        scenario="phase4_demo",
        retrieval_setting="setting_2"
    )
    print(f"Generated {rec.request_id}: status={rec.status}, first_token_ms={rec.first_token_ms}, total_ms={rec.total_ms}")
    await standin_client.close()

if __name__ == "__main__":
    asyncio.run(main())
