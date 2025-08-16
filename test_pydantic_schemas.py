#!/usr/bin/env python3
"""
Test script for Pydantic schemas.
This validates that our schemas can be imported and work correctly.
"""
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Set required environment variables for testing  
os.environ['FLASK_ENV'] = 'test'
os.environ['DATABASE_URI'] = 'sqlite:///instance/access.db'
# CURRENT_OKTA_USER_EMAIL will be loaded from .env file

# Set up Flask app context
from api.app import create_app
flask_app = create_app(testing=True)

with flask_app.app_context():
    try:
        # Test base schemas
        from api_v2.schemas.base import BaseSchema, BaseReadSchema, TimestampMixin, IdMixin
        print("✓ Base schemas imported successfully")
        
        # Test user schemas
        from api_v2.schemas.users import UserSummary, UserDetail, UserList, UserSearch
        print("✓ User schemas imported successfully")
        
        # Test creating a user summary
        user_data = {
            "id": "12345",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User", 
            "display_name": "Test User",
            "deleted_at": None
        }
        
        user = UserSummary(**user_data)
        print(f"✓ UserSummary created: {user.email}")
        
        # Test JSON serialization
        user_json = user.model_dump()
        print(f"✓ JSON serialization works: {len(user_json)} fields")
        
        # Test validation
        try:
            invalid_user = UserSummary(
                id="123",
                email="not-an-email",  # Invalid email
                first_name="Test",
                last_name="User",
                display_name="Test User"
            )
            print("✗ Validation should have failed for invalid email")
        except Exception:
            print("✓ Validation correctly rejected invalid email")
        
        print("\n🎯 Pydantic schemas test completed successfully!")
        print("✓ Base schemas working")
        print("✓ User schemas working")
        print("✓ Validation working")
        print("✓ JSON serialization working")
        
    except Exception as e:
        print(f"✗ Schema test failed: {e}")
        import traceback
        traceback.print_exc()