"""
Test OpenAPI schema generation for FastAPI app.
"""


def test_openapi_schema_generation(fastapi_client):
    """Test that OpenAPI schema is generated successfully."""
    response = fastapi_client.get("/api/v2/openapi.json")
    
    assert response.status_code == 200
    schema = response.json()
    
    # ✓ OpenAPI schema generated successfully
    # - Title: {schema.get('info', {}).get('title', 'N/A')}
    # - Version: {schema.get('info', {}).get('version', 'N/A')}
    # - Endpoints: {len(schema.get('paths', {}))}
    
    # Verify basic schema structure
    assert "info" in schema
    assert "paths" in schema
    assert "title" in schema["info"]
    assert "version" in schema["info"]
    assert len(schema["paths"]) > 0


def test_openapi_schema_has_expected_endpoints(fastapi_client):
    """Test that OpenAPI schema contains expected API endpoints."""
    response = fastapi_client.get("/api/v2/openapi.json")
    
    assert response.status_code == 200
    schema = response.json()
    
    # Check that we have paths defined
    assert "paths" in schema
    paths = schema["paths"]
    
    # - Available paths:
    # for path in schema["paths"]:
    #     methods = list(schema["paths"][path].keys())
    #     └─ {path} ({', '.join(methods).upper()})
    
    # Verify we have at least some basic endpoints
    assert len(paths) > 0
    
    # Each path should have at least one HTTP method
    for path, methods in paths.items():
        assert len(methods) > 0
        # Verify methods are valid HTTP methods (excluding 'parameters' key)
        valid_methods = {'get', 'post', 'put', 'delete', 'patch', 'options', 'head'}
        path_methods = [m for m in methods.keys() if m in valid_methods]
        assert len(path_methods) > 0
