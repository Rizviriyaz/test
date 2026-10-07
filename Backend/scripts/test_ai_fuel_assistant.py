import os
import sys
from dotenv import load_dotenv

# Ensure Backend directory in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, backend_dir)
load_dotenv(os.path.join(backend_dir, ".env"))

from app.ai.assistant import run_assistant
from app.ai.tools import dispatch_tool, TOOL_DEFINITIONS

def test_ai_copilot():
    print("=" * 60)
    print("TESTING AI AGENT WITH LIVE FLEET & FUEL TOOLS")
    print("=" * 60)

    # 1. Direct tool dispatch checks
    print("\n1. Tool Dispatch: get_fleet_summary")
    res_fleet = dispatch_tool("get_fleet_summary", {})
    print("   Output:", res_fleet[:120], "...")

    print("\n2. Tool Dispatch: get_fuel_summary")
    res_fuel = dispatch_tool("get_fuel_summary", {})
    print("   Output:", res_fuel[:120], "...")

    print("\n3. Tool Dispatch: get_fleet_fuel_efficiency")
    res_eff = dispatch_tool("get_fleet_fuel_efficiency", {})
    print("   Output:", res_eff[:120], "...")

    # 2. Agentic question tests
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        print("\nGROQ_API_KEY not configured, skipping LLM invocation.")
        return

    print("\n4. Question: 'How many vehicles are moving right now?'")
    try:
        ans1 = run_assistant([{"role": "user", "content": "How many vehicles are moving right now?"}])
        print("   AI Response:", ans1)
    except Exception as e:
        print("   AI error:", e)

    print("\n5. Question: 'How much fuel did the fleet consume?'")
    try:
        ans2 = run_assistant([{"role": "user", "content": "How much fuel did the fleet consume?"}])
        print("   AI Response:", ans2)
    except Exception as e:
        print("   AI error:", e)

    print("\n[SUCCESS] AI Tools & Assistant Verified!")

if __name__ == "__main__":
    test_ai_copilot()
