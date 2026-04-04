from django.contrib import admin
from .models import Institution, Election, Position, Candidate, Voter, Vote, ContactMessage

from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages




import csv
import random
import string

from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.http import HttpResponse


# --------------------------------
# EduVoteGH Admin Branding
# --------------------------------
admin.site.site_header = "EduVoteGH Administration"
admin.site.site_title = "EduVoteGH Admin Portal"
admin.site.index_title = "Welcome to EduVoteGH Control Panel"


admin.site.register(Institution)

admin.site.register(Position)
admin.site.register(Candidate)
admin.site.register(Voter)
admin.site.register(Vote)


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):

    list_display = ("title", "institution", "start_time", "end_time", "is_active")

    change_form_template = "admin/election_change_form.html"

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "<int:election_id>/upload-voters/",
                self.admin_site.admin_view(self.upload_voters),
                name="upload-voters",
            ),
        ]

        return custom_urls + urls

    def upload_voters(self, request, election_id):

        election = Election.objects.get(id=election_id)

        if request.method == "POST":
            csv_file = request.FILES.get("csv_file")

            if not csv_file:
                messages.error(request, "Please upload a CSV file.")
                return redirect(request.path)

            decoded_file = csv_file.read().decode("utf-8", errors="ignore").splitlines()
            reader = csv.DictReader(decoded_file)

            voters_created = 0
            credentials = []

            for row in reader:

                full_name = row.get("full_name")
                username = row.get("username")
                phone = row.get("phone")

                if not username:
                    continue

                # split full name
                first_name = full_name.split()[0] if full_name else ""
                last_name = " ".join(full_name.split()[1:]) if full_name else ""

                # Generate password
                password = 'EV' + ''.join(random.choices(
                    string.ascii_uppercase + string.digits, k=8
                ))

                user, user_created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "first_name": first_name,
                        "last_name": last_name
                    }
                )

                # Create or get voter
                voter, voter_created = Voter.objects.get_or_create(
                    user=user,
                    defaults={
                        "institution": election.institution,
                        "phone": phone
                    }
                )

                # Check if voter already belongs to this election
                if voter.elections.filter(id=election.id).exists():

                    credentials.append({
                        "full_name": full_name,
                        "username": username,
                        "phone": phone,
                        "password": "existing voter"
                    })

                else:
                    # Set password
                    user.password = make_password(password)
                    user.first_name = first_name
                    user.last_name = last_name
                    user.save()

                    voter.elections.add(election)

                    credentials.append({
                        "full_name": full_name,
                        "username": username,
                        "phone": phone,
                        "password": password
                    })

                voters_created += 1
            messages.success(
                request,
                f"{voters_created} voters uploaded successfully."
            )

            # Create CSV with credentials
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="voter_credentials.csv"'

            writer = csv.writer(response)
            writer.writerow(["full_name", "username", "phone", "password"])

            for cred in credentials:
                writer.writerow([
                    cred["full_name"],
                    cred["username"],
                    cred["phone"],
                    cred["password"]
                ])

            return response
            

        context = {
            "election": election
        }

        return render(
            request,
            "admin/upload_voters.html",
            context
        )

@admin.register(ContactMessage)
class ContactAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "email",
        "phone",
        "school",
        "role",
        "students",
        "created_at",
        "is_replied",
    )

    list_filter = ("role", "is_replied", "created_at")

    search_fields = ("name", "email", "school")

    list_editable = ("is_replied",)

    ordering = ("-created_at",)