#!/usr/bin/env python3
"""
Test suite for secure password-protected HTTP Telemetry & AI Endpoint
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set test environment
os.environ["TELEMETRY_PORT"] = "8999"
os.environ["TELEMETRY_PASSWORD"] = "super_secret_test_key_42"
os.environ["BYBIT_API_KEY"] = "zu7QF2MXivrNUpxe2y"
os.environ["BYBIT_API_SECRET"] = "mzYaF9c7tFLrLThjKsFLueoFeFyFs5c8RcYo"

import telemetry_server

def test_telemetry():
    print("=== [1/5] Testing Log Sanitizer ===")
    sample_log = (
        "2026-09-09 02:40:00 [INFO] Client initialized with BYBIT_API_KEY=zu7QF2MXivrNUpxe2y "
        "and BYBIT_API_SECRET=mzYaF9c7tFLrLThjKsFLueoFeFyFs5c8RcYo and password=super_secret_test_key_42"
    )
    clean = telemetry_server.sanitize_logs(sample_log)
    print("Cleaned Log:\n", clean)
    assert "zu7QF2MXivrNUpxe2y" not in clean, "API Key was not sanitized!"
    assert "mzYaF9c7tFLrLThjKsFLueoFeFyFs5c8RcYo" not in clean, "API Secret was not sanitized!"
    assert "super_secret_test_key_42" not in clean, "Password was not sanitized!"
    print(">>> Log Sanitizer PASSED!")

    print("\n=== [2/5] Starting Telemetry Server on Port 8999 ===")
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 8999), telemetry_server.TelemetryHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(1)

    print("\n=== [3/5] Testing Unauthenticated Access ===")
    # 1. API endpoint without auth should return 401
    try:
        urllib.request.urlopen("http://127.0.0.1:8999/api/status")
        assert False, "Should have thrown 401!"
    except urllib.error.HTTPError as e:
        print(f"Received expected status code: {e.code}")
        assert e.code == 401

    print("\n=== [4/5] Testing Authenticated Access (Query Param) ===")
    # 2. Authenticated with ?password=super_secret_test_key_42
    url = "http://127.0.0.1:8999/api/status?password=super_secret_test_key_42"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        print("Received Status JSON keys:", list(data.keys()))
        assert "server_time" in data

    print("\n=== [5/5] Testing AI Summary Endpoint (/api/ai-summary) ===")
    # 3. AI Summary endpoint
    ai_url = "http://127.0.0.1:8999/api/ai-summary?password=super_secret_test_key_42"
    with urllib.request.urlopen(ai_url) as resp:
        assert resp.status == 200
        md_text = resp.read().decode("utf-8")
        print("AI Summary excerpt:\n" + "\n".join(md_text.splitlines()[:10]))
        assert "# Bybit Multi-Pair Bot: Live Telemetry Summary" in md_text

    httpd.shutdown()
    print("\n>>> ALL TELEMETRY TESTS PASSED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    test_telemetry()
