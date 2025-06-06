from typing import Any

from flask import url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture

from api.models import (
    OktaGroup,
    OktaUser,
)
from api.operations.constraints.check_for_reason import CheckForReason


def test_invalid_reason_with_template(mocker: MockerFixture) -> None:
    """Test that the template itself and templates with placeholders are considered invalid reasons."""

    # Mock the access_config to return a specific template
    mock_access_config = mocker.patch("api.operations.constraints.check_for_reason.get_access_config")
    mock_config = mocker.MagicMock()
    mock_config.reason_template = (
        "Project: [Project Name]\nTicket: [Ticket ID]\nJustification: [Why is this access needed?]"
    )
    mock_config.reason_template_required = ["Project", "Ticket", "Justification"]
    mock_access_config.return_value = mock_config

    # Test cases
    # 1. Empty reason
    assert CheckForReason.invalid_reason(None) is True
    assert CheckForReason.invalid_reason("") is True
    assert CheckForReason.invalid_reason("   ") is True

    # 2. Template as-is (unchanged)
    template = "Project: [Project Name]\nTicket: [Ticket ID]\nJustification: [Why is this access needed?]"
    assert CheckForReason.invalid_reason(template) is True

    # 3. Template with missing fields
    template = "Project: [Project Name]\nTicket: [Ticket ID]"
    assert CheckForReason.invalid_reason(template) is True

    # 4. Template with all placeholders filled should be valid
    filled_template = "Project: My Project\nTicket: TICKET-123\nJustification: I need access to deploy code"
    assert CheckForReason.invalid_reason(filled_template) is False

    # 5. Completely different invalid reason
    valid_reason = "I need access for the new project launch"
    assert CheckForReason.invalid_reason(valid_reason) is True


def test_reason_validation_in_request_endpoint(
    client: FlaskClient,
    db: SQLAlchemy,
    mocker: MockerFixture,
    okta_group: OktaGroup,
    user: OktaUser,
) -> None:
    """Test that the API endpoints reject reasons that just contain the template."""

    # Mock the access_config to return a specific template
    mock_access_config = mocker.patch("api.views.schemas.access_requests.get_access_config")
    mock_config = mocker.MagicMock()
    mock_config.reason_template = (
        "Project: [Project Name]\nTicket: [Ticket ID]\nJustification: [Why is this access needed?]"
    )
    mock_config.reason_template_required = ["Project", "Ticket", "Justification"]
    mock_access_config.return_value = mock_config

    # Set up the group and user
    db.session.add(okta_group)
    db.session.add(user)
    db.session.commit()

    # Try creating an access request with the template as reason
    template = "Project: [Project Name]\nTicket: [Ticket ID]"

    data: dict[str, Any] = {
        "group_id": okta_group.id,
        "group_owner": False,
        "reason": template,
    }

    # Create the access request
    access_request_url = url_for("api-access-requests.access_requests")
    rep = client.post(access_request_url, json=data)

    # Should get rejected because the reason it is missing required information
    assert rep.status_code == 400

    # Try again with a filled template
    filled_template = "Project: My Project\nTicket: TICKET-123\nJustification: I need access to deploy code"
    data["reason"] = filled_template

    rep = client.post(access_request_url, json=data)
    # Should succeed with a properly filled template
    assert rep.status_code == 201
