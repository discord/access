#!/usr/bin/env python3
"""
Test OpenAPI schema generation for FastAPI app.
"""
import sys
import os
import json

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set required environment variables for testing
os.environ['FLASK_ENV'] = 'test'
os.environ['DATABASE_URI'] = 'sqlite:///instance/access.db'
os.environ['CURRENT_OKTA_USER_EMAIL'] = 'test@example.com'

# Set up Flask app context
from api.app import create_app
flask_app = create_app(testing=True)

with flask_app.app_context():
    from api_v2.main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    
    # Test OpenAPI schema generation
    try:
        response = client.get("/api/v2/openapi.json")
        if response.status_code == 200:
            schema = response.json()
            print("✓ OpenAPI schema generated successfully")
            print(f"  - Title: {schema.get('info', {}).get('title', 'N/A')}")
            print(f"  - Version: {schema.get('info', {}).get('version', 'N/A')}")
            print(f"  - Endpoints: {len(schema.get('paths', {}))}")
            
            # List the available endpoints
            if 'paths' in schema:
                print("  - Available paths:")
                for path in schema['paths']:
                    methods = list(schema['paths'][path].keys())
                    print(f"    └─ {path} ({', '.join(methods).upper()})")
        else:
            print(f"✗ OpenAPI schema failed: {response.status_code}")
            
    except Exception as e:
        print(f"✗ OpenAPI schema generation failed: {e}")
    
    print(f"\n📚 You can view the interactive API docs at:")
    print(f"   http://localhost:8000/api/v2/docs")
    print(f"   http://localhost:8000/api/v2/redoc")