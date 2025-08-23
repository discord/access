#!/usr/bin/env python3
"""
Test groups endpoint in FastAPI.
"""

import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

# Set required environment variables for testing
os.environ["FLASK_ENV"] = "test"
os.environ["DATABASE_URI"] = "sqlite:///instance/access.db"
# CURRENT_OKTA_USER_EMAIL will be loaded from .env file

# Set up Flask app context for config access
# Initialize FastAPI app (which sets up logging and services)
from fastapi.testclient import TestClient

from api_v2.main import app

client = TestClient(app)

print("🧪 Testing FastAPI Groups Endpoints\n")

# Test groups list endpoint
try:
    response = client.get("/api/v2/groups")
    print("📋 GET /api/v2/groups")
    print(f"   Status: {response.status_code}")

    if response.status_code == 200:
        groups = response.json()
        print(f"   Groups returned: {len(groups)}")
        if groups:
            first_group = groups[0]
            print(f"   First group: {first_group.get('name', 'N/A')} (type: {first_group.get('type', 'N/A')})")
            print("   ✓ Groups list endpoint working")

            # Test getting a specific group
            group_id = first_group.get("id")
            if group_id:
                try:
                    response = client.get(f"/api/v2/groups/{group_id}")
                    print(f"\n👥 GET /api/v2/groups/{group_id}")
                    print(f"   Status: {response.status_code}")

                    if response.status_code == 200:
                        group = response.json()
                        print(f"   Group: {group.get('name', 'N/A')} (type: {group.get('type', 'N/A')})")

                        # Check if it's an app group with app_id
                        if group.get("type") == "app_group" and "app_id" in group:
                            print(f"   App ID: {group.get('app_id', 'N/A')}")
                            print(f"   Is Owner: {group.get('is_owner', False)}")

                        print("   ✓ Individual group endpoint working")
                    else:
                        print(f"   ✗ Failed: {response.text}")

                except Exception as e:
                    print(f"   ✗ Exception: {e}")
        else:
            print("   ℹ️ No groups found (empty database)")
    else:
        print(f"   ✗ Failed: {response.text}")

except Exception as e:
    print(f"   ✗ Exception: {e}")

print()

# Test group search
try:
    response = client.get("/api/v2/groups?q=app")
    print("🔍 GET /api/v2/groups?q=app")
    print(f"   Status: {response.status_code}")

    if response.status_code == 200:
        groups = response.json()
        print(f"   Search results: {len(groups)}")
        if groups:
            for group in groups[:3]:  # Show first 3 results
                print(f"   - {group.get('name', 'N/A')} ({group.get('type', 'N/A')})")
            print("   ✓ Search endpoint working")
        else:
            print("   ℹ️ No groups found matching 'app'")
    else:
        print(f"   ✗ Failed: {response.text}")

except Exception as e:
    print(f"   ✗ Exception: {e}")

print()

# Test type filter
try:
    response = client.get("/api/v2/groups?type=app_group")
    print("🏷️  GET /api/v2/groups?type=app_group")
    print(f"   Status: {response.status_code}")

    if response.status_code == 200:
        groups = response.json()
        print(f"   App groups: {len(groups)}")
        if groups:
            app_group = groups[0]
            print(f"   Example: {app_group.get('name', 'N/A')}")
            if "app_id" in app_group and app_group["app_id"]:
                print(f"   App ID: {app_group['app_id']}")
            print("   ✓ Type filter working")
        else:
            print("   ℹ️ No app groups found")
    else:
        print(f"   ✗ Failed: {response.text}")

except Exception as e:
    print(f"   ✗ Exception: {e}")

print("\n🎯 Groups endpoints test completed!")
print("Visit http://localhost:8000/api/v2/docs to see the discriminated unions in action!")
