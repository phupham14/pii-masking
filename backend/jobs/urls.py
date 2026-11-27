from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health),
    path("auth/token/", views.LoginView.as_view()),            # {username, password} -> {access, refresh}
    path("auth/token/refresh/", views.RefreshView.as_view()),  # {refresh} -> {access}
    path("auth/me/", views.me),
    path("policies/", views.policies),
    path("jobs/", views.jobs),
    path("jobs/<uuid:job_id>/", views.job_detail),
    path("jobs/<uuid:job_id>/download/", views.download),
    path("jobs/<uuid:job_id>/review/", views.review),
]
