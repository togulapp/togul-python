from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Optional, TypeVar, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils.module_loading import import_string

from ..client import TogulClient
from ..config import Config

_client: Optional[TogulClient] = None

ContextBuilder = Callable[[Any], Dict[str, str]]
ViewFunc = TypeVar("ViewFunc", bound=Callable[..., Any])


def build_context(request: Any) -> Dict[str, str]:
    """Map a Django request onto the flag evaluation context.

    Maps the authenticated user's primary key to ``user_id`` and nothing
    else, matching ``togul-laravel``'s middleware. An anonymous request
    yields an empty context rather than a blank ``user_id``, so its cache
    key stays ``flag:environment`` and rule matching never sees a
    present-but-empty attribute.

    Applications that need more — a country, a plan, an experiment cohort —
    point ``settings.TOGUL["CONTEXT_BUILDER"]`` at their own function
    instead of editing this one. Every value must be a string: the API
    types ``context`` as ``additionalProperties: {type: string}``.
    """
    context: Dict[str, str] = {}

    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        context["user_id"] = str(user.pk)

    return context


def _settings() -> Dict[str, Any]:
    raw = getattr(settings, "TOGUL", None)
    if not raw or not raw.get("API_KEY") or not raw.get("ENVIRONMENT"):
        raise ImproperlyConfigured("settings.TOGUL must define at least API_KEY and ENVIRONMENT")
    return dict(raw)


def get_client() -> TogulClient:
    """Return the process-wide Togul client, building it on first use.

    ``base_url`` is intentionally not read from settings: like
    ``togul-laravel/config/togul.php``, the framework layer exposes only the
    key, environment, timeout, TTL and retry count. Construct a ``Config``
    directly to point at a non-production origin.
    """
    global _client
    if _client is None:
        raw = _settings()
        _client = TogulClient(
            Config(
                api_key=raw["API_KEY"],
                environment=raw["ENVIRONMENT"],
                timeout=raw.get("TIMEOUT", 5.0),
                cache_ttl=raw.get("CACHE_TTL", 30.0),
                retry_count=raw.get("RETRY_COUNT", 2),
            )
        )
    return _client


def reset_client() -> None:
    """Drop the cached client. Useful in tests and after a settings reload."""
    global _client
    if _client is not None:
        _client.close()
    _client = None


def _resolve_context_builder() -> ContextBuilder:
    raw = getattr(settings, "TOGUL", None) or {}
    builder = raw.get("CONTEXT_BUILDER")
    if isinstance(builder, str):
        resolved: ContextBuilder = import_string(builder)
        return resolved
    if callable(builder):
        return cast(ContextBuilder, builder)
    return build_context


class TogulMiddleware:
    """Attach ``request.togul`` and ``request.togul_context`` to every request.

    Add ``"togul.contrib.django.TogulMiddleware"`` to ``MIDDLEWARE``.
    """

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        self.get_response = get_response
        self.build_context = _resolve_context_builder()

    def __call__(self, request: Any) -> Any:
        request.togul = get_client()
        request.togul_context = self.build_context(request)
        return self.get_response(request)


def togul_flag(flag_key: str) -> Callable[[ViewFunc], ViewFunc]:
    """Gate a view behind a flag, raising ``Http404`` when it is disabled.

    This is the Django form of ``togul-laravel``'s route guard
    (``->middleware('togul:new-dashboard')`` → ``abort(404)``). Django
    middleware takes no per-route arguments, so the guard is a decorator::

        @togul_flag("new-dashboard")
        def dashboard(request):
            ...

    The context comes from ``request.togul_context`` when ``TogulMiddleware``
    already built it, and is built on demand otherwise — so the decorator
    works with or without the middleware installed.
    """

    def decorator(view: ViewFunc) -> ViewFunc:
        @functools.wraps(view)
        def wrapped(request: Any, *args: Any, **kwargs: Any) -> Any:
            context = getattr(request, "togul_context", None)
            if context is None:
                context = _resolve_context_builder()(request)

            if not get_client().evaluate(flag_key, context).enabled:
                raise Http404(f"togul: flag {flag_key!r} is disabled")

            return view(request, *args, **kwargs)

        return cast(ViewFunc, wrapped)

    return decorator
