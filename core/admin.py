from django.contrib import admin
from .models import Institution, Election, Position, Candidate, Voter, Vote, ContactMessage


admin.site.register(Institution)
admin.site.register(Election)
admin.site.register(Position)
admin.site.register(Candidate)
admin.site.register(Voter)
admin.site.register(Vote)


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