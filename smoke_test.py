import json
import traceback
import sys
from fastapi.testclient import TestClient

try:
    sys.path.append('backend')
    from main import create_app
    app = create_app()
    client = TestClient(app)
except Exception:
    print("STARTUP_ERROR")
    traceback.print_exc()
    sys.exit(1)

endpoints = [
    "/health",
    "/resume/health",
    "/assessment/health",
    "/interview/health",
    "/voice/health",
    "/dsa/health",
    "/auth/status",
    "/auth/me"
]

results = {}

for ep in endpoints:
    try:
        response = client.get(ep)
        results[ep] = {
            "status_code": response.status_code,
            "json": response.json() if "application/json" in response.headers.get("Content-Type", "") else response.text[:200]
        }
    except Exception as e:
        results[ep] = {"error": str(e)}

print("SMOKE_TEST_RESULTS")
print(json.dumps(results, indent=2))
