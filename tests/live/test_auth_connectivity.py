import os
import pytest

from daibai.core.config import Config


pytestmark = pytest.mark.skipif(
    not (os.environ.get("AUTH_CLIENT_ID") and os.environ.get("AUTH_CLIENT_SECRET") and os.environ.get("AUTH_TENANT_ID")),
    reason="Live auth test requires AUTH_CLIENT_ID, AUTH_CLIENT_SECRET, and AUTH_TENANT_ID environment variables.",
)


def test_robot_can_acquire_graph_token():
    """
    Live test: attempts to acquire a client-credentials token from the configured tenant.
    This test performs a real network call and should only be run in integration/live environments.
    """
    # Ensure msal is available
    pytest.importorskip("msal")

    cfg = Config()
    ok = cfg.validate_auth_config(fail_on_error=True)
    assert ok is True

