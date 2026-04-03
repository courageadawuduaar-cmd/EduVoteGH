from django import forms
from django.contrib.auth.forms import AuthenticationForm

class VoterLoginForm(AuthenticationForm):
    username = forms.CharField(label="Index Number or Username")
    password = forms.CharField(widget=forms.PasswordInput)

from django import forms
from .models import Election

class VoterCSVUploadForm(forms.Form):
    election = forms.ModelChoiceField(
        queryset=Election.objects.all(),
        empty_label="-- Select Election --",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    csv_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.csv,.xlsx'})
    )