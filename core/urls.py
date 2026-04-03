from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.voter_login, name='voter_login'),
    path('logout/', views.voter_logout, name='voter_logout'),
    path('dashboard/', views.vote_dashboard, name='vote_dashboard'),
    path('vote/<int:election_id>/', views.vote_page, name='vote_page'),
    path('admin-panel/', views.admin_panel, name='admin_panel'),
    path('control/logs/', views.admin_logs, name='admin_logs'),
    path('control/analytics/', views.admin_analytics, name='admin_analytics'),
    path('results/<int:election_id>/', views.election_results, name='election_results'),
    path('export-results/<int:election_id>/', views.export_results_pdf, name='export_results_pdf'),
    path('contact/', views.contact_view, name='core_contact'),
    path('upload-voters/', views.upload_voters, name='upload_voters'),
    path('live-results/<int:election_id>/', views.live_results, name='live_results'),

    # ✅ Moved out of /admin/ prefix to avoid Django admin interception
    path('api/election-stats/', views.election_stats_api, name='election_stats'),
    path('api/turnout/', views.turnout_data, name='turnout_data'),
]