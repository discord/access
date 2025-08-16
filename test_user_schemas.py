#!/usr/bin/env python3
"""
Test script for User Pydantic schemas.
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

# Set up Flask app context
from api.app import create_app
flask_app = create_app(testing=True)

with flask_app.app_context():
    try:
        # Test user schemas
        from api_v2.schemas.users import (
            UserSummary, UserBase, UserDetail, UserList, UserSearch, UserGroupMember
        )
        print("✓ User schemas imported successfully")
        
        # Test UserSummary
        user_summary = UserSummary(
            id="user123",
            email="wumpus@discord.com",
            first_name="Test",
            last_name="User",
            display_name="Test User"
        )
        print(f"✓ UserSummary: {user_summary.email}")
        
        # Test UserDetail
        user_detail = UserDetail(
            id="user456",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            email="detail@example.com",
            first_name="Detail",
            last_name="User",
            display_name="Detail User",
            manager=user_summary
        )
        print(f"✓ UserDetail: {user_detail.email} with manager {user_detail.manager.email}")
        
        # Test UserSearch
        user_search = UserSearch(q="test query", page=2, per_page=50)
        print(f"✓ UserSearch: page {user_search.page}, per_page {user_search.per_page}")
        
        # Test validation
        try:
            invalid_search = UserSearch(page=0)  # Should fail
            print("✗ Validation should have failed for page=0")
        except ValueError:
            print("✓ Validation correctly rejected page=0")
        
        print("\n🎯 User schemas test completed successfully!")
        
    except Exception as e:
        print(f"✗ User schema test failed: {e}")
        import traceback
        traceback.print_exc()