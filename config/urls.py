from django.contrib import admin
from django.urls import include, path

from apps.authentication.views import ExchangeLogidView
from apps.ghl.views import GhlRefreshHookView, GhlWebhookView
from apps.sales.views import AgentsListView

urlpatterns = [
    path("api/admin/", admin.site.urls),
    path("api/auth/", include("apps.authentication.urls")),
    path("api/ghl/", include("apps.ghl.urls")),
    path("api/leaderboards/", include("apps.sales.urls")),
    path("api/agents/", AgentsListView.as_view(), name="agents-list"),
    path("api/db/", include("apps.dbapi.urls")),
    # Public endpoints — paths match the original app exactly (no trailing slash)
    path("api/public/auth/exchange-logid", ExchangeLogidView.as_view()),
    path("api/public/hooks/ghl-webhook", GhlWebhookView.as_view()),
    path("api/public/hooks/ghl-refresh", GhlRefreshHookView.as_view()),
]
