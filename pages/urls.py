from django.urls import path
from . import views

urlpatterns = [
    path("privacy-policy/", views.privacy_policy, name="privacy"),
    path("terms-of-service/", views.terms_of_service, name="terms"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="pages_contact"),  # ✅ renamed
]