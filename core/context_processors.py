from django.utils import timezone
from .models import Institution, Election

def global_election_context(request):
    now = timezone.now()

    active_election = Election.objects.filter(
    is_active=True,
    start_time__lte=now,
    end_time__gte=now
).first()

    institution = None

    if active_election and active_election.institution:
        institution = active_election.institution

    return {
        'institution': institution,
        'active_election': active_election,
        'now': now
    }