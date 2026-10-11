"""Every connected Trade View route fails closed without subscription access."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.auth import AuthContext, get_auth, require_tradeview_subscription
from app.main import app
from app.routers.tradeview import router

@pytest.mark.parametrize('status,sub,plan', [
    ('inactive', 'sub_test', 'pro'), ('past_due', 'sub_test', 'pro'),
    ('active', None, 'pro'), ('active', 'sub_test', 'unknown'),
])
def test_policy_denies(status, sub, plan):
    with pytest.raises(HTTPException) as error:
        require_tradeview_subscription(AuthContext('u', 'o', plan, status, sub))
    assert error.value.status_code == 403

def test_policy_allows_synced_subscription():
    auth = AuthContext('u', 'o', 'basic', 'active', 'sub_test')
    assert require_tradeview_subscription(auth) is auth

@pytest.mark.parametrize('route', router.routes)
def test_every_route_denies_before_data_access(route):
    app.dependency_overrides[get_auth] = lambda: AuthContext('u', 'o', 'pro')
    path = route.path.replace('{kind}', 'customer').replace('{entity_id}', 'other-org')
    try:
        with TestClient(app) as client:
            response = client.request(next(iter(route.methods)), '/api/v1' + path, json={})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_auth, None)
