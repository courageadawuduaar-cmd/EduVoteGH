from django.shortcuts import render

def privacy_policy(request):
    return render(request, "pages/privacy_policy.html")

def terms_of_service(request):
    return render(request, "pages/terms.html")

def about(request):
    return render(request, "pages/about.html")

def contact(request):
    return render(request, "pages/contact.html")