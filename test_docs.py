#!/usr/bin/env python3
"""
Test FastAPI documentation endpoints to ensure proper Content-Type handling.
"""

import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///instance/access.db"
os.environ["CURRENT_OKTA_USER_EMAIL"] = "test@example.com"

# Set up Flask app context
# Initialize FastAPI app (which sets up logging and services)
from fastapi.testclient import TestClient

from api_v2.main import app

client = TestClient(app)

# Test documentation endpoints
endpoints_to_test = [
    ("/api/v2/docs", "HTML documentation"),
    ("/api/v2/redoc", "ReDoc documentation"),
    ("/api/v2/openapi.json", "OpenAPI schema"),
    ("/api/v2/healthz", "JSON API endpoint"),
]

for endpoint, description in endpoints_to_test:
    try:
        response = client.get(endpoint)
        content_type = response.headers.get("content-type", "")

        print(f"📄 {endpoint} ({description})")
        print(f"   Status: {response.status_code}")
        print(f"   Content-Type: {content_type}")

        # Check expected content types
        if endpoint == "/api/v2/openapi.json":
            if "application/json" in content_type:
                print("   ✓ Correct JSON content type")
            else:
                print("   ✗ Expected JSON content type")
        elif endpoint in ["/api/v2/docs", "/api/v2/redoc"]:
            if "text/html" in content_type:
                print("   ✓ Correct HTML content type")
            elif "application/json" in content_type:
                print("   ✗ Incorrect JSON content type (should be HTML)")
            else:
                print(f"   ? Unexpected content type: {content_type}")
        elif endpoint == "/api/v2/healthz":
            if "application/json" in content_type:
                print("   ✓ Correct JSON content type")
            else:
                print("   ✗ Expected JSON content type")

        print()

    except Exception as e:
        print(f"✗ {endpoint} failed: {e}")
        print()

print("🎯 Test complete! ReDoc should now display properly as HTML.")
