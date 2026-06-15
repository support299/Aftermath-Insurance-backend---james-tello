from django.urls import path

from apps.ghl.views import (
    ExchangeCodeView,
    GhlOAuthConfigView,
    GhlStatusView,
    RefreshTokenView,
    UpdateContactFromSaleView,
)

urlpatterns = [
    path("oauth-config/", GhlOAuthConfigView.as_view(), name="ghl-oauth-config"),
    path("status/", GhlStatusView.as_view(), name="ghl-status"),
    path("exchange-code/", ExchangeCodeView.as_view(), name="ghl-exchange-code"),
    path("refresh-token/", RefreshTokenView.as_view(), name="ghl-refresh-token"),
    path(
        "update-contact-from-sale/",
        UpdateContactFromSaleView.as_view(),
        name="ghl-update-contact-from-sale",
    ),
]
