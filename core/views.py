from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from .forms import VoterLoginForm
from django.contrib import messages
from .models import Election, Position, Candidate, Voter, Vote
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count
from django.db.models import Count, Max
from .models import Candidate
from django.db.models import Count, Sum
import csv
from io import TextIOWrapper
from django.contrib.auth.models import User
from core.models import Voter, Institution, Election
from django.core.mail import send_mail
import random
import string
import pandas as pd
from .forms import VoterCSVUploadForm
from django.shortcuts import render, redirect
from django.shortcuts import render
from django.http import HttpResponse

from django.shortcuts import render, get_object_or_404
from core.models import Election, Position, Candidate, Vote

from django.http import JsonResponse
from .models import Candidate, Position, Election
from .models import Candidate
from django.shortcuts import redirect
from .models import Election
from django.utils import timezone
from django.contrib.admin.views.decorators import staff_member_required
from .models import Voter

def voter_login(request):
    if request.method == "POST":
        form = VoterLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('vote_dashboard')
        else:
            messages.error(request, "Invalid username or password")
    else:
        form = VoterLoginForm()
    return render(request, 'core/login.html', {'form': form})

def voter_logout(request):
    logout(request)
    return redirect('voter_login')


from django.contrib.auth.decorators import login_required

from django.utils import timezone

@login_required
def vote_dashboard(request):
    user = request.user
    try:
        voter = Voter.objects.get(user=user)
    except Voter.DoesNotExist:
        messages.error(request, "You are not registered as a voter.")
        return redirect('voter_login')

    now = timezone.now()

    # Get truly active elections (based on time)
    elections = voter.elections.filter(
        start_time__lte=now,
        end_time__gte=now
    )

    context = {
        'voter': voter,
        'elections': elections
    }

    return render(request, 'core/dashboard.html', context)


@login_required
def vote_page(request, election_id):
    user = request.user

    # Check voter
    try:
        voter = Voter.objects.get(user=user)
    except Voter.DoesNotExist:
        messages.error(request, "You are not registered as a voter.")
        return redirect('voter_login')

    election = get_object_or_404(Election, id=election_id)
    now = timezone.now()

    # 🚫 Block before start
    if now < election.start_time:
        messages.warning(request, "This election has not started yet.")
        return redirect('home')

    # 🚫 Block after end
    if now > election.end_time:
        messages.info(request, "This election has ended.")
        return redirect('election_results', election_id=election.id)

    positions = Position.objects.filter(
        election=election
    ).prefetch_related('candidate_set')

    voted_positions = Vote.objects.filter(
        voter=voter,
        election=election
    ).values_list('position_id', flat=True)

    if request.method == "POST":

        votes_created = 0
        already_voted_all = True

        for position in positions:

            candidate_id = request.POST.get(f'position_{position.id}')

            # If voter already voted for this position
            if position.id in voted_positions:
                continue

            already_voted_all = False

            if candidate_id:
                candidate = Candidate.objects.get(id=candidate_id)

                try:
                    Vote.objects.create(
                        voter=voter,
                        candidate=candidate,
                        position=position,
                        election=election
                    )
                    votes_created += 1
                except IntegrityError:
                    continue

        if votes_created > 0:
            messages.success(request, "Your votes have been submitted successfully!")
        elif already_voted_all:
            messages.warning(request, "You have already voted in this election.")
        else:
            messages.warning(request, "No valid votes were submitted.")

        return redirect('vote_dashboard')

    context = {
        'voter': voter,
        'election': election,
        'positions': positions,
        'voted_positions': voted_positions,
    }

    return render(request, 'core/vote_page.html', context)


@login_required
def election_results(request, election_id):
    now = timezone.now()
    election = get_object_or_404(Election, id=election_id)
    results = []

    positions = Position.objects.filter(election=election)

    for position in positions:
        candidates = Candidate.objects.filter(position=position)

        candidate_list = []
        for candidate in candidates:
            votes_count = Vote.objects.filter(candidate=candidate).count()
            candidate.votes_count = votes_count
            candidate_list.append(candidate)

        winner = None
        is_tie = False

        if candidate_list:

            max_votes = max(c.votes_count for c in candidate_list)

            # If nobody voted
            if max_votes == 0:
                winner = None
            else:
                top_candidates = [
                    c for c in candidate_list if c.votes_count == max_votes
                ]

                if len(top_candidates) == 1:
                    winner = top_candidates[0]
                else:
                    is_tie = True
                    winner = top_candidates  # list of tied candidates

        results.append({
            'position': position,
            'candidates': candidate_list,
            'winner': winner,
            'is_tie': is_tie
        })

    context = {
        'election': election,
        'results': results,
        'now': now
    }

    return render(request, 'core/results.html', context)

@login_required
def upload_voters(request):
    if request.method == 'POST':
        form = VoterCSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES['csv_file']

            try:
                df = pd.read_excel(file)
            except Exception:
                messages.error(request, "Invalid Excel file. Please provide a valid .xlsx file.")
                return render(request, 'core/upload_voters.html', {'form': form})

            institution = Institution.objects.first()
            election = Election.objects.first()
            credentials = []

            required_columns = ['full_name', 'phone', 'username']

            for col in required_columns:
                if col not in df.columns:
                    messages.error(request, f"Missing required column: {col}")
                    return render(request, 'core/upload_voters.html', {'form': form})

            for index, row in df.iterrows():

                full_name = str(row['full_name']).strip()
                phone = str(row['phone']).strip()
                username = str(row['username']).strip()

                if not username:
                    continue

                # Create or get user
                user, created = User.objects.get_or_create(username=username)

                if created:
                    password = ''.join(random.choices(
                        string.ascii_letters + string.digits, k=8
                    ))
                    user.set_password(password)
                    user.first_name = full_name
                    user.save()
                else:
                    password = None  # do not change existing password

                # Create or update voter
                voter, v_created = Voter.objects.get_or_create(
                    user=user,
                    institution=institution
                )

                voter.phone = phone
                voter.elections.add(election)
                voter.save()

                credentials.append({
                    "full_name": full_name,
                    "username": username,
                    "phone": phone,
                    "password": password if password else "EXISTING_PASSWORD"
                })

            # Generate downloadable CSV
            creds_df = pd.DataFrame(credentials)
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="voter_credentials.csv"'
            creds_df.to_csv(path_or_buf=response, index=False)

            return response
    else:
        form = VoterCSVUploadForm()

    return render(request, 'core/upload_voters.html', {'form': form})

def results_data(request, election_id, position_id):

    candidates = Candidate.objects.filter(
        position_id=position_id,
        election_id=election_id
    )

    votes = []
    for candidate in candidates:
        count = Vote.objects.filter(candidate=candidate).count()
        votes.append(count)

    total_votes = Vote.objects.filter(
        election_id=election_id
    ).count()

    return JsonResponse({
        'votes': votes,
        'total_votes': total_votes
    })

def home(request):
    elections = Election.objects.all().order_by('-id')
    now = timezone.now()

    return render(request, 'core/home.html', {
        'elections': elections,
        'now': now
    })


# ================================
# ENTERPRISE ADMIN CONTROL PANEL
# ================================

@staff_member_required
def admin_panel(request):

    # 🔒 Auto-close elections whose time has passed
    expired_elections = Election.objects.filter(
        is_active=True,
        end_time__lt=timezone.now()
    )

    for expired in expired_elections:
        expired.is_active = False
        expired.save()

    elections = Election.objects.all().order_by('-created_at')
    active_election = Election.objects.filter(is_active=True).first()
    total_voters = Voter.objects.count()

    if request.method == "POST":
        election_id = request.POST.get("election_id")
        action = request.POST.get("action")

        election = Election.objects.get(id=election_id)

        if action == "activate":

            # 🔒 Only ONE active election allowed
            Election.objects.update(is_active=False)

            election.is_active = True
            election.save()

            # 📁 Audit Log
            from .models import AdminAuditLog
            AdminAuditLog.objects.create(
                admin=request.user,
                action=f"Activated election: {election.title}",
                election=election
            )

            messages.success(request, f"{election.title} is now ACTIVE.")

        elif action == "deactivate":

            election.is_active = False
            election.save()

            # 📁 Audit Log
            from .models import AdminAuditLog
            AdminAuditLog.objects.create(
                admin=request.user,
                action=f"Deactivated election: {election.title}",
                election=election
            )

            messages.warning(request, f"{election.title} has been deactivated.")

        return redirect("admin_panel")

    return render(request, "core/admin_panel.html", {
        "elections": elections,
        "active_election": active_election,
        "total_voters": total_voters,
    })

# ================================
# ADMIN AUDIT LOGS
# ================================

@staff_member_required
def admin_logs(request):

    from .models import AdminAuditLog

    logs = AdminAuditLog.objects.select_related(
        'admin',
        'election'
    ).order_by('-timestamp')

    return render(request, "core/admin_logs.html", {
        "logs": logs
    })

# ================================
# DASHBOARD ANALYTICS
# ================================

@staff_member_required
def admin_analytics(request):

    from django.db.models import Count
    from django.utils import timezone

    elections = Election.objects.all()
    total_elections = elections.count()
    active_election = Election.objects.filter(is_active=True).first()
    total_voters = Voter.objects.count()
    total_votes = Vote.objects.count()

    turnout_data = []

    for election in elections:
        turnout_data.append({
            "title": election.title,
            "turnout": election.turnout_percentage(),
        })

    context = {
        "total_elections": total_elections,
        "active_election": active_election,
        "total_voters": total_voters,
        "total_votes": total_votes,
        "turnout_data": turnout_data,
    }

    return render(request, "core/admin_analytics.html", context)