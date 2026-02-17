from django import forms
from django.contrib.auth.forms import AuthenticationForm

class VoterLoginForm(AuthenticationForm):
    username = forms.CharField(label="Index Number or Username")
    password = forms.CharField(widget=forms.PasswordInput)

class VoterCSVUploadForm(forms.Form):
    csv_file = forms.FileField(label='Select CSV file')