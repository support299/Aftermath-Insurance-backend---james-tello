from django.urls import path

from apps.dbapi.views import TableView

urlpatterns = [
    path("<str:table>/", TableView.as_view(), name="dbapi-table"),
]
