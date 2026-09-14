"""Django wiring. Copy the relevant bits into your own settings module."""

from __future__ import annotations

import os

MIDDLEWARE = [
    # ... your other middleware ...
    "togul.contrib.django.TogulMiddleware",
]

TOGUL = {
    "API_KEY": os.environ["TOGUL_API_KEY"],
    "ENVIRONMENT": os.environ.get("TOGUL_ENVIRONMENT", "production"),
    "CACHE_TTL": 30.0,
    # The default builder maps only the authenticated user's id. Point this
    # at your own function to control what lands in flag context.
    # "CONTEXT_BUILDER": "myapp.flags.build_context",
}


# Deciding inside the view — the middleware attaches the client and context:
#
#     def dashboard(request):
#         result = request.togul.evaluate("new-dashboard", request.togul_context)
#         if result.enabled:
#             return render(request, "dashboard/new.html")
#         return render(request, "dashboard/legacy.html")
#
#
# Gating the whole view — 404s when the flag is off:
#
#     from togul.contrib.django import togul_flag
#
#     @togul_flag("new-dashboard")
#     def beta_dashboard(request):
#         return render(request, "dashboard/new.html")
