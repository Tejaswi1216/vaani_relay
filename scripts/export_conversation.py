#!/usr/bin/env python3
"""
scripts/export_conversation.py: Exports full conversation transcript into clean Markdown and copies raw JSONL.
"""

import json
import os
import shutil
from pathlib import Path

TRANSCRIPT_SOURCE = Path("/mnt/c/Users/bichu/.gemini/antigravity-cli/brain/e537a193-3d8a-40be-a1fa-2246cc5bfa32/.system_generated/logs/transcript_full.jsonl")
REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "logs" / "conversation"
SUBMISSION_LOGS_DIR = REPO_ROOT / "submission" / "logs" / "conversation"

def export_conversation():
    if not TRANSCRIPT_SOURCE.exists():
        print(f"[ERROR] Source transcript not found: {TRANSCRIPT_SOURCE}")
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SUBMISSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copy raw transcript JSONL
    dst_jsonl = LOGS_DIR / "transcript_full.jsonl"
    shutil.copy2(TRANSCRIPT_SOURCE, dst_jsonl)
    shutil.copy2(TRANSCRIPT_SOURCE, SUBMISSION_LOGS_DIR / "transcript_full.jsonl")
    print(f"Copied raw transcript to {dst_jsonl}")

    # 2. Parse and generate formatted Markdown log
    md_lines = [
        "# The Relay: Full Conversation History with Coding Assistant",
        "",
        "**Project:** The Relay (Multi-Step LLM Pipeline with Rate-Limit Resilience)  ",
        "**Assistant:** Antigravity (Advanced Agentic Coding Pair Programmer)  ",
        "**Context:** Complete dialogue log covering Phase 0 Preflight through Phase 12 Pre-submission checks.  ",
        "",
        "---",
        ""
    ]

    turn_count = 0
    with open(TRANSCRIPT_SOURCE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            stype = d.get("type")
            source = d.get("source")
            content = d.get("content", "")
            step_idx = d.get("step_index", turn_count)
            created_at = d.get("created_at", "")

            if stype == "USER_INPUT":
                turn_count += 1
                # Clean up user request tags if present
                clean_content = content
                if "<USER_REQUEST>" in clean_content:
                    parts = clean_content.split("<USER_REQUEST>")
                    req_part = parts[1].split("</USER_REQUEST>")[0].strip()
                    clean_content = req_part
                
                md_lines.append(f"## User Prompt (Turn {step_idx}) — {created_at}\n")
                md_lines.append("```text")
                md_lines.append(clean_content)
                md_lines.append("```\n")

            elif stype == "PLANNER_RESPONSE" and content and isinstance(content, str) and content.strip():
                # Assistant visible response
                md_lines.append(f"### Assistant Response (Step {step_idx}) — {created_at}\n")
                md_lines.append(content.strip())
                md_lines.append("\n---\n")

    md_path = LOGS_DIR / "conversation_history.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    shutil.copy2(md_path, SUBMISSION_LOGS_DIR / "conversation_history.md")
    print(f"Wrote formatted dialogue log to {md_path} and synced to submission.")

if __name__ == "__main__":
    export_conversation()
