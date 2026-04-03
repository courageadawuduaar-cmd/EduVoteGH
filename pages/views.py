from django.shortcuts import render, redirect

def privacy_policy(request):
    return render(request, "pages/privacy_policy.html")

def terms_of_service(request):
    return render(request, "pages/terms.html")

def about(request):
    return render(request, "pages/about.html")

def contact(request):
    # ✅ Redirect to the real contact view in core
    return redirect('core_contact')