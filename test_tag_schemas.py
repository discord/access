#!/usr/bin/env python3
"""
Test script for Tag Pydantic schemas.
"""
import sys
import os
from datetime import datetime

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
        # Test tag schemas
        from api_v2.schemas.tags import TagRead, TagCreate, TagUpdate, TagSearch
        print("✓ Tag schemas imported successfully")
        
        # Test TagRead
        tag_read = TagRead(
            id="tag123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            name="test-tag",
            enabled=True,
            constraints={}
        )
        print(f"✓ TagRead: {tag_read.name}, enabled={tag_read.enabled}")
        
        # Test TagCreate
        tag_create = TagCreate(
            name="new-tag",
            enabled=True,
            constraints={"department": "engineering"}
        )
        print(f"✓ TagCreate: {tag_create.name}")
        
        # Test TagUpdate
        tag_update = TagUpdate(
            name="updated-tag",
            enabled=False
        )
        print(f"✓ TagUpdate: {tag_update.name}, enabled={tag_update.enabled}")
        
        # Test TagSearch
        tag_search = TagSearch(q="admin", page=1, per_page=25)
        print(f"✓ TagSearch: query='{tag_search.q}'")
        
        # Test validation
        try:
            invalid_search = TagSearch(per_page=150)  # Should fail (max 100)
            print("✗ Validation should have failed for per_page > 100")
        except ValueError:
            print("✓ Validation correctly rejected per_page > 100")
        
        print("\n🎯 Tag schemas test completed successfully!")
        
    except Exception as e:
        print(f"✗ Tag schema test failed: {e}")
        import traceback
        traceback.print_exc()