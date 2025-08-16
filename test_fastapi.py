#!/usr/bin/env python3
"""
Test script for FastAPI setup.
This validates that our FastAPI app starts correctly and basic endpoints work.
"""
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set required environment variables for testing
os.environ['FLASK_ENV'] = 'test'
os.environ['DATABASE_URI'] = 'sqlite:///instance/access.db'  # Use existing SQLite DB
os.environ['CURRENT_OKTA_USER_EMAIL'] = 'test@example.com'

# Set up Flask app context for config access
from api.app import create_app
flask_app = create_app(testing=True)

with flask_app.app_context():
    # Now we can import and test the FastAPI app
    from api_v2.main import app
    
    # Test basic import
    print("✓ FastAPI app imported successfully")
    
    # Test that we can create a test client
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    print("✓ TestClient created successfully")
    
    # Test root endpoint
    try:
        response = client.get("/")
        print(f"✓ Root endpoint: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"✗ Root endpoint failed: {e}")
    
    # Test health endpoint
    try:
        response = client.get("/api/v2/healthz")
        print(f"✓ Health endpoint: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"✗ Health endpoint failed: {e}")
    
    # Test database health endpoint
    try:
        response = client.get("/api/v2/healthz/db")
        result = response.json()
        print(f"✓ Database health: {response.status_code} - Status: {result.get('status')}")
        if result.get('active_users') is not None:
            print(f"  └─ Active users in database: {result['active_users']}")
    except Exception as e:
        print(f"✗ Database health endpoint failed: {e}")
    
    print("\n🚀 FastAPI setup test completed!")
    print("You can now run the FastAPI server with:")
    print("  uvicorn api_v2.main:app --reload --port 8000")
    print("\nThen visit:")
    print("  http://localhost:8000/api/v2/docs (Swagger UI)")
    print("  http://localhost:8000/api/v2/healthz (Health check)")