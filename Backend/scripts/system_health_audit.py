import os
import sys
import json
import time
import urllib.request
import urllib.error

def audit_env_and_security():
    print("=== 1. SECURITY & ENVIRONMENT AUDIT ===")
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    backend_env_exists = os.path.exists(env_path)
    print(f"Backend .env exists: {backend_env_exists}")
    
    # Check if any sensitive keys are hardcoded in python source code
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    suspicious_patterns = ["WIALON_TOKEN =", "SUPABASE_KEY =", "GROQ_API_KEY =", "OPENAI_API_KEY ="]
    findings = []
    
    for root, _, files in os.walk(app_dir):
        for f in files:
            if f.endswith(".py"):
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                    content = file.read()
                    for pattern in suspicious_patterns:
                        if pattern in content:
                            findings.append(f"Hardcoded secret pattern '{pattern}' found in {f}")
                            
    if findings:
        print("  [WARN] SECURITY WARNINGS:")
        for finding in findings:
            print(f"    - {finding}")
    else:
        print("  [OK] No hardcoded API keys/tokens found in Backend source files.")

def audit_backend_endpoints():
    print("\n=== 2. BACKEND API ENDPOINT & DATA VALIDATION AUDIT ===")
    base_url = "http://localhost:8000"
    endpoints = [
        ("/api/health", "GET"),
        ("/api/dashboard/summary", "GET"),
        ("/api/dashboard/mileage", "GET"),
        ("/api/fleet/health", "GET"),
        ("/api/fleet/utilization", "GET"),
        ("/api/fleet/trips", "GET"),
        ("/api/fleet/safety", "GET"),
        ("/api/fleet/alerts", "GET"),
        ("/api/fleet/groups", "GET"),
        ("/api/vehicles", "GET"),
    ]
    
    results = {}
    for ep, method in endpoints:
        start_time = time.time()
        try:
            req = urllib.request.Request(f"{base_url}{ep}", method=method)
            with urllib.request.urlopen(req, timeout=35) as response:
                latency = round((time.time() - start_time) * 1000, 2)
                status_code = response.getcode()
                body = json.loads(response.read().decode('utf-8'))
                results[ep] = {
                    "status": status_code,
                    "latency_ms": latency,
                    "body_type": type(body).__name__,
                    "keys": list(body.keys()) if isinstance(body, dict) else len(body),
                    "data": body
                }
                print(f"  [OK] {ep} -> Status: {status_code}, Latency: {latency} ms")
        except Exception as e:
            results[ep] = {"status": "ERROR", "error": str(e)}
            print(f"  [FAIL] {ep} -> Error: {e}")
            
    return results

def audit_data_integrity(api_results):
    print("\n=== 3. DATA INTEGRITY & 24H CALCULATIONS AUDIT ===")
    # 1. Summary Endpoint Validation
    summary_res = api_results.get("/api/dashboard/summary", {}).get("data", {})
    summary_data = summary_res.get("summary", {}) if isinstance(summary_res, dict) else {}
    
    total_vehicles = summary_data.get("total_vehicles", 0)
    moving = summary_data.get("moving", 0)
    idling = summary_data.get("idling", 0)
    stopped = summary_data.get("stopped", 0)
    offline = summary_data.get("offline", 0)
    
    status_sum = moving + idling + stopped + offline
    print(f"  Vehicle Counts: Total={total_vehicles}, Moving={moving}, Idling={idling}, Stopped={stopped}, Offline={offline}")
    print(f"  Sum Check (moving+idling+stopped+offline == total): {status_sum == total_vehicles} ({status_sum}/{total_vehicles})")
    
    # 2. Metric Checks
    dist_km = summary_data.get("total_distance_km", 0)
    idle_min = summary_data.get("total_idle_minutes", 0)
    trips = summary_data.get("total_trips", 0)
    safety_events = summary_data.get("total_safety_events", 0)
    
    print(f"  24h Distance: {dist_km} km")
    print(f"  24h Idle Time: {idle_min} min ({round(idle_min/60, 2)} hrs)")
    print(f"  24h Trips: {trips}")
    print(f"  24h Safety Events: {safety_events}")
    
    # Check for hardcoded metrics or NaN values
    is_valid = (
        total_vehicles > 0 and 
        isinstance(dist_km, (int, float)) and 
        isinstance(idle_min, (int, float)) and
        isinstance(trips, int) and
        isinstance(safety_events, int)
    )
    print(f"  Data Integrity Valid: {is_valid}")

def audit_ai_assistant():
    print("\n=== 4. AI ASSISTANT AUDIT ===")
    base_url = "http://localhost:8000"
    chat_url = f"{base_url}/api/ai/chat"
    
    payload = json.dumps({"message": "How many vehicles are currently moving?"}).encode('utf-8')
    req = urllib.request.Request(chat_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    
    try:
        start_time = time.time()
        with urllib.request.urlopen(req, timeout=30) as response:
            latency = round((time.time() - start_time) * 1000, 2)
            status_code = response.getcode()
            body = json.loads(response.read().decode('utf-8'))
            print(f"  [OK] AI Chat Endpoint -> Status: {status_code}, Latency: {latency} ms")
            reply = body.get("reply", "") or body.get("response", "") or str(body)
            print(f"  AI Reply Snippet: {reply[:150]}...")
    except Exception as e:
        print(f"  [WARN] AI Chat Endpoint returned error/degraded state: {e}")

if __name__ == "__main__":
    audit_env_and_security()
    api_res = audit_backend_endpoints()
    audit_data_integrity(api_res)
    audit_ai_assistant()
