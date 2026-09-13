from __future__ import annotations

import pytest

django = pytest.importorskip("django")

from django.conf import settings  # noqa: E402


@pytest.fixture(autouse=True)
def django_settings():
    if not settings.configured:
        settings.configure(DEBUG=True, TOGUL={})
    from togul.contrib.django import reset_client

    reset_client()
    yield
    reset_client()


class FakeUser:
    def __init__(self, pk, is_authenticated=True):
        self.pk = pk
        self.is_authenticated = is_authenticated


class FakeRequest:
    def __init__(self, user=None):
        self.user = user


def test_build_context_maps_an_authenticated_user():
    from togul.contrib.django import build_context

    assert build_context(FakeRequest(FakeUser("u-1"))) == {"user_id": "u-1"}


def test_build_context_is_empty_for_anonymous_requests():
    from togul.contrib.django import build_context

    assert build_context(FakeRequest(FakeUser("u-1", is_authenticated=False))) == {}
    assert build_context(FakeRequest(None)) == {}
    assert build_context(object()) == {}


def test_get_client_requires_settings():
    from django.core.exceptions import ImproperlyConfigured

    from togul.contrib.django import get_client

    settings.TOGUL = {}
    with pytest.raises(ImproperlyConfigured):
        get_client()


def test_get_client_builds_config_from_settings_and_is_a_singleton():
    from togul.contrib.django import get_client

    settings.TOGUL = {
        "API_KEY": "k",
        "ENVIRONMENT": "staging",
        "CACHE_TTL": 5.0,
        "RETRY_COUNT": 4,
    }
    client = get_client()
    assert client.config.api_key == "k"
    assert client.config.environment == "staging"
    assert client.config.cache_ttl == 5.0
    assert client.config.retry_count == 4
    assert get_client() is client


def test_base_url_is_not_configurable_from_django_settings():
    from togul.config import DEFAULT_BASE_URL
    from togul.contrib.django import get_client

    # Parity with togul-laravel/config/togul.php, which has no base-url key.
    settings.TOGUL = {
        "API_KEY": "k",
        "ENVIRONMENT": "production",
        "BASE_URL": "https://ignored.test",
    }
    assert get_client().config.base_url == DEFAULT_BASE_URL


def test_middleware_attaches_client_and_context():
    from togul.contrib.django import TogulMiddleware

    settings.TOGUL = {"API_KEY": "k", "ENVIRONMENT": "production"}
    request = FakeRequest(FakeUser("u-7"))

    middleware = TogulMiddleware(lambda req: "response")
    assert middleware(request) == "response"
    assert request.togul_context == {"user_id": "u-7"}
    assert request.togul.config.environment == "production"


def test_context_builder_can_be_overridden_with_a_callable():
    from togul.contrib.django import TogulMiddleware

    settings.TOGUL = {
        "API_KEY": "k",
        "ENVIRONMENT": "production",
        "CONTEXT_BUILDER": lambda request: {"plan": "premium"},
    }
    request = FakeRequest(FakeUser("u-7"))
    TogulMiddleware(lambda req: None)(request)
    assert request.togul_context == {"plan": "premium"}


# --- togul_flag decorator: the Django form of Laravel's route guard ---


def stub_evaluation(monkeypatch, *, enabled: bool, seen: list):
    """Replace the singleton client with one that records its calls."""
    from togul.result import EvaluateResult

    class StubClient:
        config = None

        def evaluate(self, flag_key, context=None):
            seen.append((flag_key, context))
            return EvaluateResult(flag_key, enabled, "boolean", enabled, "rule_match")

    import togul.contrib.django as module

    monkeypatch.setattr(module, "get_client", lambda: StubClient())


def test_togul_flag_runs_the_view_when_the_flag_is_enabled(monkeypatch):
    from togul.contrib.django import togul_flag

    settings.TOGUL = {"API_KEY": "k", "ENVIRONMENT": "production"}
    seen: list = []
    stub_evaluation(monkeypatch, enabled=True, seen=seen)

    @togul_flag("new-dashboard")
    def view(request):
        return "rendered"

    assert view(FakeRequest(FakeUser("u-3"))) == "rendered"
    assert seen == [("new-dashboard", {"user_id": "u-3"})]


def test_togul_flag_raises_404_when_the_flag_is_disabled(monkeypatch):
    from django.http import Http404

    from togul.contrib.django import togul_flag

    settings.TOGUL = {"API_KEY": "k", "ENVIRONMENT": "production"}
    stub_evaluation(monkeypatch, enabled=False, seen=[])

    @togul_flag("new-dashboard")
    def view(request):
        return "rendered"

    with pytest.raises(Http404):
        view(FakeRequest(FakeUser("u-3")))


def test_togul_flag_reuses_the_context_the_middleware_already_built(monkeypatch):
    from togul.contrib.django import togul_flag

    settings.TOGUL = {"API_KEY": "k", "ENVIRONMENT": "production"}
    seen: list = []
    stub_evaluation(monkeypatch, enabled=True, seen=seen)

    @togul_flag("f")
    def view(request):
        return "rendered"

    request = FakeRequest(FakeUser("u-3"))
    request.togul_context = {"plan": "premium"}
    view(request)

    assert seen == [("f", {"plan": "premium"})]


def test_togul_flag_preserves_the_view_name_and_arguments(monkeypatch):
    from togul.contrib.django import togul_flag

    settings.TOGUL = {"API_KEY": "k", "ENVIRONMENT": "production"}
    stub_evaluation(monkeypatch, enabled=True, seen=[])

    @togul_flag("f")
    def dashboard(request, slug, page=1):
        return f"{slug}:{page}"

    assert dashboard.__name__ == "dashboard"
    assert dashboard(FakeRequest(None), "reports", page=3) == "reports:3"
