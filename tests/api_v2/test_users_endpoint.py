"""
Test users endpoint in FastAPI.
"""


def test_users_list_endpoint(fastapi_client):
    """Test that users list endpoint works correctly."""
    response = fastapi_client.get("/api/v2/users")
    
    # GET /api/v2/users
    # Status: {response.status_code}
    
    assert response.status_code == 200
    users = response.json()
    
    # Users returned: {len(users)}
    assert isinstance(users, list)
    
    if users:
        first_user = users[0]
        # First user: {first_user.get('email', 'N/A')}
        # ✓ Users list endpoint working
        
        # Verify user structure
        assert "email" in first_user
    # else: No users found (empty database)


def test_me_endpoint(fastapi_client):
    """Test the @me endpoint for getting current user."""
    response = fastapi_client.get("/api/v2/users/@me")
    
    # GET /api/v2/users/@me
    # Status: {response.status_code}
    
    assert response.status_code == 200
    user = response.json()
    
    # Current user: {user.get('email', 'N/A')}
    # ✓ @me endpoint working
    
    # Verify user structure
    assert "email" in user
    assert user["email"] is not None


def test_user_search_endpoint(fastapi_client):
    """Test user search functionality."""
    response = fastapi_client.get("/api/v2/users?q=a")
    
    # GET /api/v2/users?q=a
    # Status: {response.status_code}
    
    assert response.status_code == 200
    users = response.json()
    
    # Search results: {len(users)}
    assert isinstance(users, list)
    
    if users:
        # Verify search results have email field
        for user in users:
            assert "email" in user
        # ✓ Search endpoint working
    # else: No users found matching 'a'
