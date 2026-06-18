from django.urls import path

from apps.sales.views import LeaderboardView

urlpatterns = [
    path("", LeaderboardView.as_view(), name="leaderboard"),
]
