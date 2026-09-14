# Togul Python SDK

Official Python SDK for Togul feature flags.

## Install

```bash
pip install togul
```

Framework integrations are extras:

```bash
pip install "togul[django]"
pip install "togul[fastapi]"
```

## Quick start (sync)

```python
"""Minimal synchronous usage."""

from __future__ import annotations

import os

from togul import Config, TogulClient

config = Config(
    api_key=os.environ["TOGUL_API_KEY"],
    environment=os.environ.get("TOGUL_ENVIRONMENT", "production"),
)

with TogulClient(config) as client:
    result = client.evaluate("new-dashboard", {"user_id": "u-123", "plan": "premium"})
    print(f"{result.flag_key}: enabled={result.enabled} value={result.value!r} ({result.reason})")
```

`TogulClient` is blocking. Use it from Django views, Celery tasks, scripts, or
anywhere else that isn't running an event loop. The `with` block closes the
underlying HTTP connection pool on exit; if you pass your own `httpx.Client`,
the SDK leaves it open and you own its lifecycle.

See [`examples/basic.py`](examples/basic.py).

## Async

For FastAPI, Starlette, or any asyncio application, use `AsyncTogulClient`:

```python
from togul import AsyncTogulClient, Config

config = Config(api_key="...", environment="production")

async with AsyncTogulClient(config) as client:
    result = await client.evaluate("new-dashboard", {"user_id": "u-123"})
```

The API mirrors `TogulClient` exactly, `evaluate` just needs an `await`.
`invalidate_cache()` and `invalidate_flag()` stay synchronous on both clients:
they only touch the in-memory cache under a lock, so there's nothing to await.

## Real-time updates

Both clients cache evaluation results for `cache_ttl` seconds. A stream
client keeps that cache fresh by invalidating entries as flags change,
instead of waiting for the TTL to expire.

Sync, run on a background thread:

```python
import threading

from togul import Config, TogulClient, TogulStreamClient

config = Config(api_key="...", environment="production")
client = TogulClient(config)
stream = TogulStreamClient(config, client.cache)

threading.Thread(target=stream.connect, daemon=True).start()
# ... use client.evaluate(...) as usual; stream.stop() to shut it down.
```

Async, run as a task:

```python
import asyncio

from togul import AsyncTogulClient, AsyncTogulStreamClient, Config

config = Config(api_key="...", environment="production")
client = AsyncTogulClient(config)
stream = AsyncTogulStreamClient(config, client.cache)

task = asyncio.create_task(stream.connect())
# ... await client.evaluate(...) as usual; stream.stop() then cancel the task.
```

`connect()` never returns on its own. It reconnects with exponential backoff
(starting at 1s, doubling up to a 30s cap, reset after a clean disconnect)
until `stop()` is called. A 401 or 403 response is the one exception: it is
re-raised out of `connect()` immediately, because reconnecting with a
rejected key cannot fix anything.

The FastAPI integration below wires the async stream client into the
application lifespan automatically; you don't need to manage the task by
hand there.

## Django

Add the middleware and a `TOGUL` settings block:

```python
MIDDLEWARE = [
    # ... your other middleware ...
    "togul.contrib.django.TogulMiddleware",
]

TOGUL = {
    "API_KEY": os.environ["TOGUL_API_KEY"],
    "ENVIRONMENT": os.environ.get("TOGUL_ENVIRONMENT", "production"),
    "CACHE_TTL": 30.0,
    # "CONTEXT_BUILDER": "myapp.flags.build_context",
}
```

| Key | Default | Notes |
|---|---|---|
| `API_KEY` | required | |
| `ENVIRONMENT` | required | |
| `TIMEOUT` | `5.0` | HTTP timeout in seconds. |
| `CACHE_TTL` | `30.0` | Seconds an evaluation stays cached. |
| `RETRY_COUNT` | `2` | Total request attempts (see below), not extra retries. |
| `CONTEXT_BUILDER` | the built-in `build_context` | Dotted path or callable; see below. |

There is deliberately no `BASE_URL` key. This matches `togul-laravel`'s
`config/togul.php`, which exposes the same narrow set of framework-layer
options. If you need a non-production origin, construct a `Config` directly
(see [`examples/basic.py`](examples/basic.py)) rather than going through
Django settings.

`TogulMiddleware` attaches `request.togul` (a `TogulClient`) and
`request.togul_context` (built by `CONTEXT_BUILDER`, or the default
`build_context` if unset) to every request. The default `build_context` maps
*only* the authenticated user's primary key to `user_id`, and omits it
entirely for an anonymous request rather than sending a blank value. If your
flags need anything else — a plan, a country, an experiment cohort — point
`CONTEXT_BUILDER` at your own function; don't edit the default.

```python
def dashboard(request):
    result = request.togul.evaluate("new-dashboard", request.togul_context)
    if result.enabled:
        return render(request, "dashboard/new.html")
    return render(request, "dashboard/legacy.html")
```

For gating an entire view, use the `@togul_flag("key")` decorator instead.
It raises `Http404` when the flag is disabled, and works with or without the
middleware installed (it builds context on demand if `request.togul_context`
isn't there):

```python
from togul.contrib.django import togul_flag


@togul_flag("new-dashboard")
def beta_dashboard(request):
    return render(request, "dashboard/new.html")
```

See [`examples/django_settings.py`](examples/django_settings.py).

## FastAPI

```python
from fastapi import Depends, FastAPI

from togul import AsyncTogulClient, Config
from togul.contrib.fastapi import create_lifespan, get_togul

config = Config(api_key="...", environment="production")

app = FastAPI(lifespan=create_lifespan(config))


@app.get("/dashboard")
async def dashboard(
    user_id: str,
    togul: AsyncTogulClient = Depends(get_togul),
) -> dict:
    result = await togul.evaluate("new-dashboard", {"user_id": user_id})
    return {"variant": "new" if result.enabled else "legacy", "reason": result.reason}
```

`create_lifespan(config, *, stream=True, client=None)` builds an
`AsyncTogulClient`, stores it on `app.state.togul`, and — since `stream`
defaults to `True` — starts an `AsyncTogulStreamClient` as a background task
so SSE events invalidate the cache in real time. Both are shut down cleanly
on application exit. Pass `client=` to supply your own pre-built
`AsyncTogulClient` (for tests, or a custom `httpx.AsyncClient`); the lifespan
still closes it on shutdown. Pass `stream=False` to skip the background
connection and rely on `cache_ttl` alone.

`get_togul` is a plain `Depends()` provider that reads `app.state.togul`.

See [`examples/fastapi_app.py`](examples/fastapi_app.py).

## Configuration reference

Every field of `Config`:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `api_key` | `str` | required | Environment-scoped API key. |
| `environment` | `str` | required | Environment key, e.g. `"production"`. |
| `timeout` | `float` | `5.0` | HTTP request timeout, in seconds. |
| `cache_ttl` | `float` | `30.0` | How long an evaluation result stays cached, in seconds. |
| `retry_count` | `int` | `2` | **Total** request attempts, not extra retries — `2` means the SDK issues at most two requests, i.e. one retry. |
| `base_url` | `str` | `"https://api.togul.io"` | API origin. Trailing slashes are stripped. |

`Config` is a frozen dataclass: build a new one to change settings.

## Caching and invalidation

Both clients keep an in-memory, thread-safe TTL cache of `EvaluateResult`s.
An entry is dropped lazily, the first time it's read after `cache_ttl`
seconds have passed, or if it carries a malformed (empty) `value_type`.

The cache key is `flag_key:environment`, followed by `:key=value` for every
context pair, with context keys sorted so call order never changes the key.
For example, `evaluate("new-dashboard", {"plan": "premium", "user_id": "u-123"})`
against environment `production` produces
`new-dashboard:production:plan=premium:user_id=u-123`.

`invalidate_cache()` clears the whole cache; `invalidate_flag(flag_key)`
clears only entries for that flag. A `TogulStreamClient` /
`AsyncTogulStreamClient` calls these automatically as SSE events arrive: an
event naming a `flag_key` invalidates just that flag, and a malformed event
with no key flushes the whole cache. Register `on_cache_invalidated(...)` on
either the evaluation client or the stream client to observe invalidations
yourself, e.g. for logging.

## Error handling

Every error the SDK raises is a `TogulError`. `TogulAPIError` is the subclass
raised when the API itself returns a non-2xx response; it carries
`status_code` and, when the response body has one, `error_code`.

Retries apply only to HTTP `429` and any `5xx` response, plus network-level
failures. Every other `4xx` (400, 401, 403, 404, ...) raises immediately with
no retry. When retries are exhausted, the client raises a `TogulError`
wrapping the last failure.

**There is no fail-open fallback.** Unlike SDKs that silently return a
default value when evaluation fails, this SDK never guesses on your behalf:
a failed `evaluate()` call raises, and it's up to your code to decide what
happens next — return a default, log and re-raise, fall back to cached
behavior, whatever fits your product. If you want fail-open behavior, wrap
`evaluate()` in a `try`/`except TogulError` yourself.

## Requirements

Python 3.9+. Django integration requires Django 4.2+. FastAPI integration
requires FastAPI 0.100+.

## License

MIT. See [LICENSE](LICENSE).
