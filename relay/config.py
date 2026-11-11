from pathlib import Path
import os
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TEXT_DIR = DATA_DIR / "text"
PAPERS_DIR = DATA_DIR / "papers"
QUESTIONS_FILE = REPO_ROOT / "questions" / "questions.yaml"
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_CSV = RESULTS_DIR / "results.csv"
LEDGER_FILE = RESULTS_DIR / "ledger.jsonl"
STANDIN_PARAMS_FILE = REPO_ROOT / "standin" / "params.json"

PAPERS_INFO = {
    "attention_v7": {
        "title": "Attention Is All You Need",
        "arxiv_id": "1706.03762",
        "version": "v7",
    },
    "moshi_v1": {
        "title": "Moshi: a speech-text foundation model for real-time dialogue",
        "arxiv_id": "2410.00037",
        "version": "v1",
    },
    "dynchunk_v2": {
        "title": "Dynamic Chunking for End-to-End Hierarchical Sequence Modeling",
        "arxiv_id": "2507.07955",
        "version": "v2",
    },
}

# R1: The Groq key lives only in ~/.config/relay/groq.env. Loaded only here.
_GROQ_ENV_PATH = Path(os.path.expanduser("~/.config/relay/groq.env"))
if _GROQ_ENV_PATH.exists():
    load_dotenv(dotenv_path=_GROQ_ENV_PATH)

def get_groq_api_key() -> str:
    """Safely retrieves the GROQ_API_KEY environment variable."""
    return os.environ.get("GROQ_API_KEY", "")

# Operational constraints
MODEL_NAME = "qwen/qwen3.8-27b"
GROQ_API_BASE = "https://api.groq.com/openai/v1"
STANDIN_API_BASE = "http://127.0.0.1:8000/v1"

# Latency limits from AGENTS.md section 4
PATIENCE_TTFT_MS = 8000
PATIENCE_TOTAL_MS = 30000
FAST_TARGET_P95_MS = 3000
