#!/usr/bin/env python3
"""
Script to run the FastAPI development server.
This sets up the necessary environment and runs uvicorn.
"""
import os
import sys

# Set required environment variables for development
os.environ['FLASK_ENV'] = 'development'
os.environ['DATABASE_URI'] = 'sqlite:///instance/access.db'
os.environ['CURRENT_OKTA_USER_EMAIL'] = 'test@example.com'
os.environ['CLIENT_ORIGIN_URL'] = 'http://localhost:3000'

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting FastAPI development server...")
    print("📄 API docs will be available at: http://localhost:8000/api/v2/docs")
    print("🔍 Health check at: http://localhost:8000/api/v2/healthz")
    print("⚠️  Press CTRL+C to stop the server")
    print()
    
    # Set up Flask app context first
    from api.app import create_app
    flask_app = create_app(testing=True)
    
    with flask_app.app_context():
        # Now run uvicorn
        uvicorn.run(
            "api_v2.main:app",
            host="127.0.0.1",
            port=8000,
            reload=True,
            log_level="info"
        )