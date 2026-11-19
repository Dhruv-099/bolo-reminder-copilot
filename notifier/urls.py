from django.urls import path
from . import views

app_name = 'notifier'

urlpatterns = [
    path('', views.home_page_view, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('event/<int:event_id>/preview/', views.event_preview, name='event_preview'),
    path('event/<int:event_id>/dispatch/', views.event_dispatch, name='event_dispatch'),
    path('dispatch-all/', views.dispatch_all_due, name='dispatch_all_due'),
    path('respond/<int:event_id>/', views.respond, name='respond'),
    path('analytics/', views.analytics, name='analytics'),
]
