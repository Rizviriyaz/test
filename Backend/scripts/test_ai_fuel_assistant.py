"""
LIVE test: calls real Wialon, Supabase and (when GROQ_API_KEY is set) Groq. Needs a populated Backend/.env.

Fails (exit code 1) when a tool returns an error or invalid JSON, or the assistant raises or answers empty.
"""
import json
import os
import sys
from dotenv import load_dotenv

# Ensure Backend directory in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, backend_dir)
load_dotenv(os.path.join(backend_dir, ".env"))

from app.ai.assistant import run_assistant
from app.ai.tools import dispatch_tool

def test_ai_copilot() -> int:
    print("=" * 60)
    print("TESTING AI AGENT WITH LIVE FLEET & FUEL TOOLS")
    print("=" * 60)
    failures = 0

    # 1. Direct tool dispatch checks
    for step, tool in enumerate(("get_fleet_summary", "get_fuel_summary", "get_fleet_fuel_efficiency"), start=1):
        print(f"\n{step}. Tool Dispatch: {tool}")
        raw = dispatch_tool(tool, {})
        print("   Output:", raw[:120], "...")
        try:
            result = json.loads(raw)
        except ValueError:
            result = None
        if not isinstance(result, dict) or "error" in result:
            failures += 1
            print(f"   [FAIL] {tool} returned an error or invalid JSON")
        else:
            print(f"   [PASS] {tool}")

    # 2. Agentic question tests
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        print("\n[SKIP] GROQ_API_KEY not configured, skipping LLM invocation.")
        return failures

    questions = ("How many vehicles are moving right now?", "How much fuel did the fleet consume?")
    for step, question in enumerate(questions, start=4):
        print(f"\n{step}. Question: '{question}'")
        try:
            answer = run_assistant([{"role": "user", "content": question}])
            print("   AI Response:", answer)
            if not answer or not str(answer).strip():
                failures += 1
                print("   [FAIL] empty answer")
            else:
                print("   [PASS] answered")
        except Exception as e:
            failures += 1
            print("   [FAIL] AI error:", e)

    return failures

if __name__ == "__main__":
    failed = test_ai_copilot()
    if failed:
        print(f"\n[FAILED] {failed} AI tool/assistant check(s) failed")
        sys.exit(1)
    print("\n[SUCCESS] AI Tools & Assistant Verified!")
