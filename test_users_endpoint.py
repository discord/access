#!/usr/bin/env python3
"""
Test users endpoint in FastAPI.
"""

import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///instance/access.db"
# CURRENT_OKTA_USER_EMAIL will be loaded from .env file

# Initialize FastAPI app (which sets up logging and services)
from fastapi.testclient import TestClient
from api_v2.main import app

client = TestClient(app)

print("🧪 Testing FastAPI Users Endpoints\n")

# Test users list endpoint
try:
    response = client.get("/api/v2/users")
    print("📋 GET /api/v2/users")
    print(f"   Status: {response.status_code}")

    if response.status_code == 200:
        users = response.json()
        print(f"   Users returned: {len(users)}")
        if users:
            first_user = users[0]
            print(f"   First user: {first_user.get('email', 'N/A')}")
            print("   ✓ Users list endpoint working")
        else:
            print("   ℹ️ No users found (empty database)")
    else:
        print(f"   ✗ Failed: {response.text}")

except Exception as e:
    print(f"   ✗ Exception: {e}")

print()

# Test @me endpoint
try:
    response = client.get("/api/v2/users/@me")
    print("👤 GET /api/v2/users/@me")
    print(f"   Status: {response.status_code}")

    if response.status_code == 200:
        user = response.json()
        print(f"   Current user: {user.get('email', 'N/A')}")
        print("   ✓ @me endpoint working")
    else:
        print(f"   ✗ Failed: {response.text}")

except Exception as e:
    print(f"   ✗ Exception: {e}")

print()

# Test user search
try:
    response = client.get("/api/v2/users?q=a")
    print("🔍 GET /api/v2/users?q=a")
    print(f"   Status: {response.status_code}")

    if response.status_code == 200:
        users = response.json()
        print(f"   Search results: {len(users)}")
        if users:
            print("   ✓ Search endpoint working")
        else:
            print("   ℹ️ No users found matching 'a'")
    else:
        print(f"   ✗ Failed: {response.text}")

except Exception as e:
    print(f"   ✗ Exception: {e}")

print("\n🎯 User endpoints test completed!")
print("Visit http://localhost:8000/api/v2/docs to see interactive API docs")
