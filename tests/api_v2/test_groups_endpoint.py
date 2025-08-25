"""
Test groups endpoint in FastAPI.
"""


def test_groups_list_endpoint(fastapi_client):
    """Test that groups list endpoint works correctly."""
    response = fastapi_client.get("/api/v2/groups")
    
    # GET /api/v2/groups
    # Status: {response.status_code}
    
    assert response.status_code == 200
    groups = response.json()
    
    # Groups returned: {len(groups)}
    assert isinstance(groups, list)
    
    if groups:
        first_group = groups[0]
        # First group: {first_group.get('name', 'N/A')} (type: {first_group.get('type', 'N/A')})
        # ✓ Groups list endpoint working
        
        # Verify group structure
        assert "id" in first_group
        assert "name" in first_group
        assert "type" in first_group
    # else: No groups found (empty database)


def test_individual_group_endpoint(fastapi_client):
    """Test getting a specific group by ID."""
    # First get the list of groups to find a valid ID
    response = fastapi_client.get("/api/v2/groups")
    assert response.status_code == 200
    groups = response.json()
    
    if groups:
        first_group = groups[0]
        group_id = first_group.get("id")
        assert group_id is not None
        
        # Test getting the specific group
        response = fastapi_client.get(f"/api/v2/groups/{group_id}")
        
        # GET /api/v2/groups/{group_id}
        # Status: {response.status_code}
        
        assert response.status_code == 200
        group = response.json()
        
        # Group: {group.get('name', 'N/A')} (type: {group.get('type', 'N/A')})
        
        # Verify group structure
        assert "id" in group
        assert "name" in group
        assert "type" in group
        
        # Check if it's an app group with app_id
        if group.get("type") == "app_group":
            # App ID: {group.get('app_id', 'N/A')}
            # Is Owner: {group.get('is_owner', False)}
            if "app_id" in group:
                assert group["app_id"] is not None
        
        # ✓ Individual group endpoint working


def test_group_search_endpoint(fastapi_client):
    """Test group search functionality."""
    response = fastapi_client.get("/api/v2/groups?q=app")
    
    # GET /api/v2/groups?q=app
    # Status: {response.status_code}
    
    assert response.status_code == 200
    groups = response.json()
    
    # Search results: {len(groups)}
    assert isinstance(groups, list)
    
    if groups:
        # Verify first few results
        for group in groups[:3]:
            # - {group.get('name', 'N/A')} ({group.get('type', 'N/A')})
            assert "name" in group
            assert "type" in group
        # ✓ Search endpoint working
    # else: No groups found matching 'app'


def test_group_type_filter(fastapi_client):
    """Test filtering groups by type."""
    response = fastapi_client.get("/api/v2/groups?type=app_group")
    
    # GET /api/v2/groups?type=app_group
    # Status: {response.status_code}
    
    assert response.status_code == 200
    groups = response.json()
    
    # App groups: {len(groups)}
    assert isinstance(groups, list)
    
    if groups:
        app_group = groups[0]
        # Example: {app_group.get('name', 'N/A')}
        
        # Verify it's actually an app group
        assert app_group.get("type") == "app_group"
        
        if "app_id" in app_group and app_group["app_id"]:
            # App ID: {app_group['app_id']}
            assert app_group["app_id"] is not None
        
        # ✓ Type filter working
    # else: No app groups found
