# ============================================================
# Standard Library
# ============================================================
import csv
import json
import os
import random
import string
import uuid
from datetime import datetime
from io import TextIOWrapper

# ============================================================
# Django Core
# ============================================================
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

# ============================================================
# Third Party
# ============================================================
import pandas as pd

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Circle, Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ============================================================
# Local Models & Forms
# ============================================================
from .forms import VoterLoginForm, VoterCSVUploadForm
from .models import (
    ActivityLog,
    AdminAuditLog,
    Candidate,
    ContactMessage,
    Election,
    Institution,
    Position,
    Vote,
    Voter,
)


# ------------------------
# Voter Authentication
# ------------------------
def voter_login(request):
    if request.user.is_authenticated:
        return redirect('vote_dashboard')

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


# ------------------------
# Dashboard
# ------------------------
@login_required
def vote_dashboard(request):
    try:
        voter = Voter.objects.select_related('institution').get(user=request.user)
    except Voter.DoesNotExist:
        messages.error(request, "You are not registered as a voter.")
        return redirect('voter_login')

    now = timezone.now()

    active_elections = voter.elections.filter(
        is_active=True,
        start_time__lte=now,
        end_time__gte=now
    ).annotate(
        candidate_count=Count('candidate', distinct=True)  # ✅ distinct=True added
    ).filter(
        candidate_count__gt=0
    )

    context = {
        'voter': voter,
        'elections': active_elections
    }

    return render(request, 'core/dashboard.html', context)


# ------------------------
# Vote Page
# ------------------------

@login_required
def vote_page(request, election_id):

    try:
        voter = Voter.objects.get(user=request.user)
    except Voter.DoesNotExist:
        messages.error(request, "You are not registered as a voter.")
        return redirect('voter_login')

    election = get_object_or_404(
        Election,
        id=election_id,
        voters=voter
    )

    # Election status checks
    if not election.is_live:
        if timezone.now() < election.start_time:
            messages.warning(request, "This election has not started yet.")
            return redirect('vote_dashboard')

        if timezone.now() > election.end_time:
            messages.info(request, "This election has ended.")
            return redirect('election_results', election_id=election.id)

        if not election.is_active:
            messages.warning(request, "This election has been disabled by the administrator.")
            return redirect('vote_dashboard')

    positions = Position.objects.filter(
        election=election
    ).prefetch_related('candidate_set__user')

    # ✅ Check if election has any positions or candidates at all
    if not positions.exists():
        messages.warning(request, "This election has no positions set up yet.")
        return redirect('vote_dashboard')

    has_candidates = any(
        position.candidate_set.exists() for position in positions
    )
    if not has_candidates:
        messages.warning(request, "This election has no candidates yet.")
        return redirect('vote_dashboard')

    voted_positions = set(
        Vote.objects.filter(voter=voter, election=election)
        .values_list('position_id', flat=True)
    )

    # Check if voter has already voted in ALL positions
    all_position_ids = set(positions.values_list('id', flat=True))
    already_voted_all = all_position_ids == voted_positions

    if already_voted_all:
        messages.info(request, "You have already completed voting in this election.")
        return redirect('vote_dashboard')

    # --------------------------------
    # HANDLE POST
    # --------------------------------
    if request.method == "POST":

        # AJAX SINGLE VOTE
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({
                    "success": False,
                    "message": "Invalid request format."
                })

            position_id  = data.get('position_id')
            candidate_id = data.get('candidate_id')

            if not position_id or not candidate_id:
                return JsonResponse({
                    "success": False,
                    "message": "Invalid data."
                })

            if int(position_id) in voted_positions:
                return JsonResponse({
                    "success": False,
                    "message": "You have already voted for this position."
                })

            candidate = get_object_or_404(
                Candidate,
                id=candidate_id,
                position_id=position_id
            )

            try:
                with transaction.atomic():
                    Vote.objects.create(
                        voter=voter,
                        candidate=candidate,
                        position=candidate.position,
                        election=election,
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT')
                    )
                return JsonResponse({
                    "success": True,
                    "message": "Vote recorded successfully.",
                    "position_id": position_id
                })
            except IntegrityError:
                return JsonResponse({
                    "success": False,
                    "message": "Vote could not be recorded. Please try again."
                })

        # NORMAL FORM SUBMIT
        votes_created = 0
        candidate_map = {
            str(c.id): c
            for c in Candidate.objects.filter(position__in=positions)
        }

        with transaction.atomic():
            for position in positions:
                if position.id in voted_positions:
                    continue

                candidate_id = request.POST.get(f'position_{position.id}')
                candidate    = candidate_map.get(candidate_id)

                if candidate:
                    try:
                        Vote.objects.create(
                            voter=voter,
                            candidate=candidate,
                            position=position,
                            election=election,
                            ip_address=request.META.get('REMOTE_ADDR'),
                            user_agent=request.META.get('HTTP_USER_AGENT')
                        )
                        votes_created += 1
                    except IntegrityError:
                        continue

        if votes_created > 0:
            # Collect all receipt codes for this voter in this election
            receipts = Vote.objects.filter(
                voter=voter,
                election=election
            ).values_list('receipt_code', flat=True)

            receipt_list = " | ".join(receipts)

            messages.success(
                request,
                f"Your votes have been submitted successfully! "
                f"Your receipt codes: {receipt_list}"
            )
            return redirect('vote_receipt', election_id=election.id)

        else:
            messages.warning(request, "No valid votes were submitted. Please select a candidate for each position.")

        return redirect('vote_dashboard')

    # --------------------------------
    # PAGE LOAD
    # --------------------------------
    context = {
        'voter': voter,
        'election': election,
        'positions': positions,
        'voted_positions': voted_positions,
        'total_positions': all_position_ids.__len__(),
        'votes_remaining': len(all_position_ids - voted_positions),
    }

    return render(request, 'core/vote_page.html', context)

@login_required
def election_results(request, election_id):

    # 🔒 Allow superusers and staff (auditors) only
    if not request.user.is_staff and not request.user.is_superuser:
        messages.error(request, "You do not have permission to view results.")
        return redirect('vote_dashboard')

    now = timezone.now()
    election = get_object_or_404(Election, id=election_id)

    # Auditors can only view results of closed or ended elections
    # Superusers/admins can view results at any time
    if not request.user.is_superuser:
        if not election.is_closed and not election.is_active:
            messages.warning(request, "Results are not available for this election yet.")
            return redirect('vote_dashboard')

    positions = Position.objects.filter(
        election=election
    ).prefetch_related('candidate_set__user')

    # Total registered voters for this election
    total_registered_voters = election.voters.count()

    # Total votes cast across all positions in this election
    total_votes_cast = Vote.objects.filter(election=election).count()

    # Turnout percentage
    turnout_percentage = (
        round((total_votes_cast / total_registered_voters) * 100, 1)
        if total_registered_voters > 0 else 0
    )

    results = []

    for position in positions:

        # Single query per position using annotation
        candidates = Candidate.objects.filter(
            position=position
        ).annotate(
            votes_count=Count('vote', distinct=True)
        ).order_by('-votes_count')

        position_total_votes = sum(c.votes_count for c in candidates)

        candidate_list = []
        for candidate in candidates:
            candidate.percentage = (
                round((candidate.votes_count / position_total_votes) * 100, 1)
                if position_total_votes > 0 else 0
            )
            candidate_list.append(candidate)

        # Determine winner or tie
        winner = None
        is_tie = False

        if candidate_list:
            max_votes = candidate_list[0].votes_count  # already sorted desc

            if max_votes > 0:
                top_candidates = [
                    c for c in candidate_list if c.votes_count == max_votes
                ]
                if len(top_candidates) == 1:
                    winner = top_candidates[0]
                else:
                    is_tie = True
                    winner = top_candidates

        results.append({
            'position': position,
            'candidates': candidate_list,
            'winner': winner,
            'is_tie': is_tie,
            'position_total_votes': position_total_votes
        })

    context = {
        'election': election,
        'results': results,
        'total_registered_voters': total_registered_voters,
        'total_votes_cast': total_votes_cast,
        'turnout_percentage': turnout_percentage,
        'now': now,
        'is_auditor': request.user.is_staff and not request.user.is_superuser,
    }

    return render(request, 'core/results.html', context)

# ------------------------
# def upload_voters
# ------------------------
import pandas as pd
import random
import string
import csv

from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.contrib import messages
from django.shortcuts import render
from .forms import VoterCSVUploadForm
from .models import Institution, Election, Voter
from django.contrib.auth.models import User

@login_required
def upload_voters(request):
    if request.method == 'POST':
        form = VoterCSVUploadForm(request.POST, request.FILES)

        if form.is_valid():
            file = request.FILES['csv_file']
            election = form.cleaned_data['election']         # ✅ from form
            institution = election.institution               # ✅ derived from election

            # 📂 Read file
            try:
                if file.name.endswith('.xlsx'):
                    df = pd.read_excel(file, engine="openpyxl")
                elif file.name.endswith('.csv'):
                    df = pd.read_csv(file)
                else:
                    raise ValueError("Invalid file format")
            except Exception:
                messages.error(request, "Invalid file. Upload a valid .xlsx or .csv file.")
                return render(request, 'core/upload_voters.html', {'form': form})

            # Required columns
            required_columns = ['full_name', 'phone', 'username']
            for col in required_columns:
                if col not in df.columns:
                    messages.error(request, f"Missing column: {col}")
                    return render(request, 'core/upload_voters.html', {'form': form})

            users_to_create = []
            credentials = []
            skipped = []

            existing_usernames = set(User.objects.values_list('username', flat=True))

            for _, row in df.iterrows():
                full_name = str(row['full_name']).strip()
                phone     = str(row['phone']).strip()
                username  = str(row['username']).strip()

                if not username or username in existing_usernames:
                    skipped.append(username)
                    continue

                password = 'EV' + ''.join(random.choices(string.digits, k=6))
                user = User(
                    username=username,
                    first_name=full_name,
                    password=make_password(password)
                )
                users_to_create.append(user)
                existing_usernames.add(username)

                credentials.append({
                    "full_name": full_name,
                    "username": username,
                    "phone": phone,
                    "password": password
                })

            # Bulk create users
            User.objects.bulk_create(users_to_create)

            # Fetch created users
            created_usernames = [c['username'] for c in credentials]
            user_map = {
                u.username: u
                for u in User.objects.filter(username__in=created_usernames)
            }

            # Build voter objects
            voters_to_create = []
            for cred in credentials:
                voters_to_create.append(Voter(
                    user=user_map[cred["username"]],
                    institution=institution,        # ✅ from election
                    phone=cred["phone"]
                ))

            Voter.objects.bulk_create(voters_to_create)

            # Assign voters to the selected election ✅
            new_voters = Voter.objects.filter(user__username__in=created_usernames)
            for voter in new_voters:
                voter.elections.add(election)

            # Generate credentials CSV
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = (
                f'attachment; filename="{election.title}_voter_credentials.csv"'
            )

            writer = csv.writer(response)
            writer.writerow(['full_name', 'username', 'phone', 'password', 'election'])

            for cred in credentials:
                writer.writerow([
                    cred['full_name'],
                    cred['username'],
                    cred['phone'],
                    cred['password'],
                    election.title          # ✅ election name in CSV
                ])

            if skipped:
                writer.writerow([])
                writer.writerow(['Skipped (already exist or invalid)'])
                for s in skipped:
                    writer.writerow([s])

            messages.success(
                request,
                f"Successfully uploaded {len(credentials)} voters to '{election.title}'. "
                f"Skipped {len(skipped)}."
            )
            return response

    else:
        form = VoterCSVUploadForm()

    return render(request, 'core/upload_voters.html', {'form': form})

# ================================
# ENTERPRISE ADMIN CONTROL PANEL
# ================================

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.contrib.admin.views.decorators import staff_member_required

from .models import Election, Voter, Vote, AdminAuditLog

# ------------------------
# ADMIN PANEL
# ------------------------
@staff_member_required
def admin_panel(request):
    now = timezone.now()

    # Auto-close expired elections
    Election.objects.filter(is_active=True, end_time__lt=now).update(is_active=False)

    # Handle POST actions
    if request.method == "POST":
        election_id = request.POST.get("election_id")
        action = request.POST.get("action")

        election = get_object_or_404(Election, id=election_id)

        with transaction.atomic():
            if action == "activate":
                election.is_active = True
                election.save()

                AdminAuditLog.objects.create(
                    admin=request.user,
                    action=f"Activated election: {election.title}",
                    election=election
                )
                messages.success(request, f"'{election.title}' is now ACTIVE.")

            elif action == "deactivate":
                election.is_active = False
                election.save()

                AdminAuditLog.objects.create(
                    admin=request.user,
                    action=f"Deactivated election: {election.title}",
                    election=election
                )
                messages.warning(request, f"'{election.title}' has been deactivated.")

        return redirect("admin_panel")

    # Fetch all elections ordered by newest first
    elections = Election.objects.all().order_by('-created_at')

    # ✅ All currently active elections (not just one)
    active_elections = Election.objects.filter(is_active=True)

    # Build stats per election
    election_stats = []
    for election in elections:
        voters = election.voters.count()
        votes = Vote.objects.filter(election=election).count()
        remaining = voters - votes
        turnout = round((votes / voters) * 100, 2) if voters > 0 else 0

        election_stats.append({
            "election": election,
            "voters": voters,
            "votes": votes,
            "remaining": remaining,
            "turnout": turnout
        })

    # Recent audit logs
    logs = AdminAuditLog.objects.select_related('admin').order_by('-timestamp')[:5]

    return render(request, "core/admin_panel.html", {
        "elections": elections,
        "active_elections": active_elections,      # ✅ queryset, not .first()
        "active_elections_count": active_elections.count(),  # ✅ handy for the template
        "election_stats": election_stats,
        "logs": logs
    })

# ------------------------
# ADMIN AUDIT LOGS
# ------------------------
@staff_member_required
def admin_logs(request):
    from django.core.paginator import Paginator

    logs = AdminAuditLog.objects.select_related(
        'admin', 'election'
    ).order_by('-timestamp')

    paginator = Paginator(logs, 50)
    page = request.GET.get('page')
    logs = paginator.get_page(page)

    return render(request, "core/admin_logs.html", {"logs": logs})


# ------------------------
# DASHBOARD ANALYTICS
# ------------------------
@staff_member_required
def admin_analytics(request):
    elections = Election.objects.all().order_by('-created_at')
    now = timezone.now()

    # ✅ All active elections, not just the first one
    active_elections = Election.objects.filter(is_active=True)
    active_elections_count = active_elections.count()

    total_elections = elections.count()
    total_voters = Voter.objects.count()
    total_votes = Vote.objects.count()

    # Overall turnout percentage across all elections
    overall_turnout = round((total_votes / total_voters) * 100, 2) if total_voters > 0 else 0

    # Per-election stats
    election_stats = []
    for election in elections:
        voters = election.voters.count()
        votes = Vote.objects.filter(election=election).count()
        turnout = round((votes / voters) * 100, 2) if voters > 0 else 0

        election_stats.append({
            "title": election.title,
            "institution": election.institution.name,
            "voters": voters,
            "votes": votes,
            "turnout": turnout,
            "is_active": election.is_active,
            "is_closed": election.is_closed,
            "start_time": election.start_time,
            "end_time": election.end_time,
        })

    # Turnout data for chart — title + turnout percentage per election
    turnout_chart_data = {
        "labels": [s["title"] for s in election_stats],
        "values": [s["turnout"] for s in election_stats],
    }

    # Votes per election for bar chart
    votes_chart_data = {
        "labels": [s["title"] for s in election_stats],
        "values": [s["votes"] for s in election_stats],
    }

    context = {
        "total_elections": total_elections,
        "active_elections": active_elections,           # ✅ queryset not .first()
        "active_elections_count": active_elections_count,
        "total_voters": total_voters,
        "total_votes": total_votes,
        "overall_turnout": overall_turnout,
        "election_stats": election_stats,
        "turnout_chart_data": turnout_chart_data,       # ✅ ready for Chart.js
        "votes_chart_data": votes_chart_data,           # ✅ ready for Chart.js
    }

    return render(request, "core/admin_analytics.html", context)


def add_watermark(canvas_obj, doc):
    canvas_obj.saveState()

    canvas_obj.setFont("Helvetica-Bold", 60)
    canvas_obj.setFillColorRGB(0.9, 0.9, 0.9)  # Light grey
    canvas_obj.translate(300, 400)
    canvas_obj.rotate(45)

    canvas_obj.drawCentredString(0, 0, "OFFICIAL RESULT")

    canvas_obj.restoreState()

def export_results_pdf(request, election_id):
    election = get_object_or_404(Election, id=election_id)
    positions = Position.objects.filter(election=election)

    # Calculations
    total_registered_voters = Voter.objects.filter(elections=election).count()
    total_votes_cast = Vote.objects.filter(election=election).count()
    turnout_percentage = (
        round((total_votes_cast / total_registered_voters) * 100, 2)
        if total_registered_voters > 0 else 0
    )
    verification_id = str(uuid.uuid4()).split("-")[0].upper()
    generated_on = datetime.now().strftime('%B %d, %Y at %H:%M')

    # Colors
    DARK_GREEN  = colors.HexColor('#1B5E20')
    GOLD        = colors.HexColor('#C9A84C')
    LIGHT_GREEN = colors.HexColor('#E8F5E9')
    LIGHT_GOLD  = colors.HexColor('#FDF6E3')
    DARK_GREY   = colors.HexColor('#2c2c2c')
    MID_GREY    = colors.HexColor('#888888')
    LIGHT_GREY  = colors.HexColor('#f5f5f5')
    WHITE       = colors.white

    # PDF response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="{election.title}_Official_Results.pdf"'
    )

    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=50,
        leftMargin=50,
        topMargin=70,
        bottomMargin=70,
    )

    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Normal'],
        fontSize=20,
        fontName='Helvetica-Bold',
        textColor=DARK_GREEN,
        alignment=1,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=11,
        fontName='Helvetica',
        textColor=MID_GREY,
        alignment=1,
        spaceAfter=4,
    )
    cert_title_style = ParagraphStyle(
        'CertTitle',
        parent=styles['Normal'],
        fontSize=13,
        fontName='Helvetica-Bold',
        textColor=DARK_GREEN,
        alignment=1,
        spaceAfter=6,
    )
    section_style = ParagraphStyle(
        'Section',
        parent=styles['Normal'],
        fontSize=12,
        fontName='Helvetica-Bold',
        textColor=WHITE,
        alignment=0,
        spaceAfter=0,
        leftIndent=8,
    )
    normal_center = ParagraphStyle(
        'NormalCenter',
        parent=styles['Normal'],
        fontSize=10,
        alignment=1,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontSize=10,
        leading=16,
        alignment=1,
        textColor=DARK_GREY,
    )

    # ─────────────────────────────────────────
    # ─────────────────────────────────────────
    # LOGO — institution logo first, fallback to EduVoteGH logo
    # ─────────────────────────────────────────
    logo_path = None
    logo_url = None

    if election.institution.logo:
        try:
            # Try local path first (works locally)
            local_path = election.institution.logo.path
            if os.path.exists(local_path):
                logo_path = local_path
        except Exception:
            # On Render/Cloudinary, .path fails — use URL instead
            try:
                logo_url = election.institution.logo.url
            except Exception:
                pass

    if logo_path:
        # Local file — use directly
        try:
            logo = Image(logo_path, width=90, height=90)
            logo.hAlign = "CENTER"
            elements.append(logo)
            elements.append(Spacer(1, 8))
        except Exception:
            pass

    elif logo_url:
        # Cloudinary URL — download it into memory first
        try:
            import requests as req
            img_response = req.get(logo_url, timeout=10)
            if img_response.status_code == 200:
                from io import BytesIO
                img_buffer = BytesIO(img_response.content)
                logo = Image(img_buffer, width=90, height=90)
                logo.hAlign = "CENTER"
                elements.append(logo)
                elements.append(Spacer(1, 8))
        except Exception:
            pass

    else:
        # No institution logo — fall back to EduVoteGH PNG logo
        for logo_filename in ['logo.png', 'eduvote-logo..png']:
            for base_dir in ['staticfiles', 'static']:
                candidate = os.path.join(
                    settings.BASE_DIR, base_dir, "images", logo_filename
                )
                if os.path.exists(candidate):
                    try:
                        logo = Image(candidate, width=90, height=90)
                        logo.hAlign = "CENTER"
                        elements.append(logo)
                        elements.append(Spacer(1, 8))
                    except Exception:
                        pass
                    break
    # ─────────────────────────────────────────
    # HEADER
    # ─────────────────────────────────────────
    elements.append(Paragraph(
        election.institution.name.upper(), title_style
    ))
    elements.append(Paragraph("EduVoteGH Electoral Commission", subtitle_style))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "OFFICIAL CERTIFICATE OF DECLARATION OF RESULTS",
        cert_title_style
    ))

    # Gold divider line
    elements.append(Spacer(1, 8))
    divider = Table([['']], colWidths=[6.3*inch])
    divider.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
        ('LINEABOVE', (0, 0), (-1, -1), 0.5, DARK_GREEN),
    ]))
    elements.append(divider)
    elements.append(Spacer(1, 16))

    # Election info
    info_data = [
        [Paragraph('<b>Election:</b>', styles['Normal']),
         Paragraph(election.title, styles['Normal'])],
        [Paragraph('<b>Institution:</b>', styles['Normal']),
         Paragraph(election.institution.name, styles['Normal'])],
        [Paragraph('<b>Period:</b>', styles['Normal']),
         Paragraph(
             f"{election.start_time.strftime('%b %d, %Y %H:%M')} — "
             f"{election.end_time.strftime('%b %d, %Y %H:%M')}",
             styles['Normal']
         )],
        [Paragraph('<b>Date of Issue:</b>', styles['Normal']),
         Paragraph(generated_on, styles['Normal'])],
        [Paragraph('<b>Reference No:</b>', styles['Normal']),
         Paragraph(f"EVGH-{verification_id}", styles['Normal'])],
    ]
    info_table = Table(info_data, colWidths=[1.8*inch, 4.5*inch])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))

    # ─────────────────────────────────────────
    # SUMMARY BOX
    # ─────────────────────────────────────────
    summary_header = Table(
        [[Paragraph('ELECTION SUMMARY', section_style)]],
        colWidths=[6.3*inch]
    )
    summary_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_GREEN),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_header)

    summary_data = [
        ['TOTAL REGISTERED VOTERS', str(total_registered_voters)],
        ['TOTAL VALID VOTES CAST',  str(total_votes_cast)],
        ['VOTER TURNOUT',           f"{turnout_percentage}%"],
    ]
    summary_table = Table(summary_data, colWidths=[4.5*inch, 1.8*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GREEN),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [WHITE, LIGHT_GREEN]),
        ('BOX', (0, 0), (-1, -1), 0.5, DARK_GREEN),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (0, -1), 12),
        ('TEXTCOLOR', (1, 2), (1, 2), DARK_GREEN),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 30))

    # ─────────────────────────────────────────
    # RESULTS PER POSITION
    # ─────────────────────────────────────────
    for position in positions:

        # Position header
        pos_header = Table(
            [[Paragraph(f'POSITION: {position.name.upper()}', section_style)]],
            colWidths=[6.3*inch]
        )
        pos_header.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), DARK_GREEN),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
        ]))
        elements.append(pos_header)

        candidates = Candidate.objects.filter(
            position=position
        ).annotate(
            total_votes=Count('vote', distinct=True)
        ).order_by('-total_votes')

        if not candidates.exists():
            elements.append(Paragraph(
                "No candidates registered for this position.",
                styles['Normal']
            ))
            elements.append(Spacer(1, 20))
            continue

        total_pos_votes = sum(c.total_votes for c in candidates)

        # Table header row
        table_data = [[
            Paragraph('<b>Candidate</b>', styles['Normal']),
            Paragraph('<b>Votes</b>', styles['Normal']),
            Paragraph('<b>Percentage</b>', styles['Normal']),
            Paragraph('<b>Status</b>', styles['Normal']),
        ]]

        for index, candidate in enumerate(candidates):
            percentage = (
                round((candidate.total_votes / total_pos_votes) * 100, 2)
                if total_pos_votes > 0 else 0
            )
            name = candidate.user.get_full_name() or candidate.user.username
            is_winner = (index == 0 and candidate.total_votes > 0)

            status = Paragraph(
                '<b><font color="#1B5E20">WINNER ✓</font></b>',
                styles['Normal']
            ) if is_winner else Paragraph('—', styles['Normal'])

            name_para = Paragraph(
                f'<b>{name}</b>' if is_winner else name,
                styles['Normal']
            )

            table_data.append([
                name_para,
                str(candidate.total_votes),
                f"{percentage}%",
                status,
            ])

        results_table = Table(
            table_data,
            colWidths=[3.0*inch, 1.0*inch, 1.2*inch, 1.1*inch]
        )

        # Build row styles
        table_style = [
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), DARK_GREY),
            ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            # Grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('BOX', (0, 0), (-1, -1), 0.5, DARK_GREEN),
            # Alignment
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 1), (0, -1), 10),
            ('TOPPADDING', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
            # Alternating rows
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GREY]),
        ]

        # Highlight winner row
        if len(table_data) > 1:
            table_style += [
                ('BACKGROUND', (0, 1), (-1, 1), LIGHT_GOLD),
                ('LINEAFTER', (0, 1), (-1, 1), 0, WHITE),
            ]

        results_table.setStyle(TableStyle(table_style))
        elements.append(results_table)
        elements.append(Spacer(1, 25))

    # ─────────────────────────────────────────
    # CERTIFICATION BLOCK
    # ─────────────────────────────────────────
    elements.append(PageBreak())

    cert_header = Table(
        [[Paragraph('OFFICIAL CERTIFICATION', section_style)]],
        colWidths=[6.3*inch]
    )
    cert_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_GREEN),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
    ]))
    elements.append(cert_header)
    elements.append(Spacer(1, 20))

    elements.append(Paragraph(
        "This is to certify that the foregoing results constitute the official "
        "and final declaration of the election conducted under the authority of "
        "the <b>EduVoteGH Electoral Commission</b>. The results have been "
        "verified and are hereby declared authentic.",
        body_style
    ))
    elements.append(Spacer(1, 20))

    elements.append(Paragraph(
        f"<b>Reference Number:</b> EVGH-{verification_id}",
        normal_center
    ))
    elements.append(Paragraph(
        f"<b>Date of Certification:</b> {generated_on}",
        normal_center
    ))
    elements.append(Spacer(1, 40))

    # Signature lines
    sig_data = [[
        Paragraph('_______________________<br/><b>Returning Officer</b><br/>'
                  '<font size="9" color="grey">EduVoteGH Commission</font>',
                  normal_center),
        Paragraph('_______________________<br/><b>Institution Head</b><br/>'
                  f'<font size="9" color="grey">{election.institution.name}</font>',
                  normal_center),
        Paragraph('_______________________<br/><b>Date</b><br/>'
                  f'<font size="9" color="grey">{datetime.now().strftime("%B %d, %Y")}</font>',
                  normal_center),
    ]]
    sig_table = Table(sig_data, colWidths=[2.1*inch, 2.1*inch, 2.1*inch])
    sig_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(sig_table)
    elements.append(Spacer(1, 40))

    # ─────────────────────────────────────────
    # QR CODE
    # ─────────────────────────────────────────
    # ✅ Clean single-line string — no newlines, no special chars
    verification_data = (
        f"EduVoteGH | Election: {election.title} | "
        f"Ref: EVGH-{verification_id} | "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    qr_code = qr.QrCodeWidget(verification_data)
    bounds = qr_code.getBounds()
    qr_width  = bounds[2] - bounds[0]
    qr_height = bounds[3] - bounds[1]
    qr_drawing = Drawing(
        120, 120,
        transform=[120./qr_width, 0, 0, 120./qr_height, 0, 0]
    )
    qr_drawing.add(qr_code)

    elements.append(Paragraph("<b>Scan QR Code to Verify</b>", normal_center))
    elements.append(Spacer(1, 6))

    qr_table = Table([[qr_drawing]], colWidths=[6.3*inch])
    qr_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(qr_table)
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        f"<font size='8' color='grey'>Ref: EVGH-{verification_id}</font>",
        normal_center
    ))

    # ─────────────────────────────────────────
    # WATERMARK + BORDER
    # ─────────────────────────────────────────
    def add_watermark(canvas_obj, doc):
        canvas_obj.saveState()

        # Double border
        canvas_obj.setLineWidth(3)
        canvas_obj.setStrokeColor(colors.HexColor('#1B5E20'))
        canvas_obj.rect(20, 20, A4[0]-40, A4[1]-40)
        canvas_obj.setLineWidth(1)
        canvas_obj.setStrokeColor(colors.HexColor('#C9A84C'))
        canvas_obj.rect(26, 26, A4[0]-52, A4[1]-52)

        # Watermark text
        canvas_obj.setFont("Helvetica-Bold", 65)
        canvas_obj.setFillColor(colors.HexColor('#1B5E20'))
        canvas_obj.setFillAlpha(0.06)
        canvas_obj.translate(A4[0]/2, A4[1]/2)
        canvas_obj.rotate(45)
        canvas_obj.drawCentredString(0, 0, "EDUVOTEGH")
        canvas_obj.rotate(-45)
        canvas_obj.translate(-A4[0]/2, -A4[1]/2)

        canvas_obj.restoreState()

        # Footer
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(colors.HexColor('#888888'))
        canvas_obj.drawCentredString(
            A4[0]/2, 12,
            f"EduVoteGH Electoral Commission  |  "
            f"Reference: EVGH-{verification_id}  |  "
            f"Page {doc.page}"
        )

    doc.build(
        elements,
        onFirstPage=add_watermark,
        onLaterPages=add_watermark
    )

    return response

def contact_view(request):
    success = False

    if request.method == "POST":
        name     = request.POST.get("name", "").strip()
        email    = request.POST.get("email", "").strip()
        school   = request.POST.get("school", "").strip()
        role     = request.POST.get("role", "").strip()
        phone    = request.POST.get("phone", "").strip()
        students = request.POST.get("students", "").strip()
        message  = request.POST.get("message", "").strip()

        # Basic validation
        if not name or not email or not message:
            messages.error(request, "Name, email and message are required.")
            return render(request, "core/contact.html", {"success": False})

        # Save to database
        ContactMessage.objects.create(
            name=name,
            email=email,
            school=school,
            role=role,
            phone=phone,
            students=int(students) if students.isdigit() else None,
            message=message
        )

        # Send email notification
        send_mail(
            subject=f"New EduVoteGH Enquiry from {name} — {school}",
            message=(
                f"Name: {name}\n"
                f"Email: {email}\n"
                f"School: {school}\n"
                f"Role: {role}\n"
                f"Phone: {phone}\n"
                f"Students: {students}\n\n"
                f"Message:\n{message}"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=["eduvote.gh@gmail.com"],
            fail_silently=True
        )

        success = True

    return render(request, "core/contact.html", {"success": success})


def home(request):
    now = timezone.now()
    elections = Election.objects.filter(
        is_active=True
    ).order_by('start_time')

    return render(request, 'core/home.html', {
        'elections': elections,
        'now': now
    })

def election_stats_api(request):
    active_elections = Election.objects.filter(is_active=True)

    if not active_elections.exists():
        return JsonResponse({"labels": [], "votes": []})

    labels = []
    votes = []

    for election in active_elections:
        candidates = Candidate.objects.filter(
            election=election
        ).annotate(
            vote_count=Count('vote', distinct=True)
        )

        if not candidates.exists():
            continue

        for c in candidates:
            labels.append(
                f"{c.user.get_full_name() or c.user.username} ({election.title})"
            )
            votes.append(c.vote_count)  # ✅ 0 is valid, still shows the bar

    return JsonResponse({
        "labels": labels,
        "votes": votes
    })


def live_results(request, election_id):

    election = Election.objects.get(id=election_id)

    candidates = Candidate.objects.filter(election=election)

    data = []

    for c in candidates:

        vote_count = Vote.objects.filter(
            election=election,
            candidate=c
        ).count()

        data.append({
            "name": c.user.username,
            "votes": vote_count
        })

    return JsonResponse(data, safe=False)



from django.http import JsonResponse

def turnout_data(request):
    active_elections = Election.objects.filter(is_active=True)

    if not active_elections.exists():
        return JsonResponse({
            "votes_cast": 0,
            "remaining_voters": 0,
            "turnout_percentage": 0
        })

    # Count only voters & votes in active elections
    total_voters = Voter.objects.filter(
        elections__in=active_elections
    ).distinct().count()

    total_votes = Vote.objects.filter(
        election__in=active_elections
    ).count()

    remaining = total_voters - total_votes

    turnout_percentage = (
        round((total_votes / total_voters) * 100, 2)
        if total_voters > 0 else 0
    )

    return JsonResponse({
        "votes_cast": total_votes,
        "remaining_voters": remaining,
        "turnout_percentage": turnout_percentage
    })


def verify_vote(request):
    result = None
    code = None
    error = None

    if request.method == "POST":
        code = request.POST.get("receipt_code", "").strip().upper()

        if not code:
            error = "Please enter a receipt code."
        else:
            try:
                vote = Vote.objects.select_related(
                    'election', 'position', 'voter__user'
                ).get(receipt_code=code)

                result = {
                    "status": "Recorded",
                    "election": vote.election.title,
                    "position": vote.position.name,
                    "time_cast": vote.timestamp.strftime("%B %d, %Y at %I:%M %p"),
                    "institution": vote.election.institution.name,
                }

            except Vote.DoesNotExist:
                error = "No vote found with that receipt code. Please check and try again."

    return render(request, "core/verify_vote.html", {
        "result": result,
        "code": code,
        "error": error,
    })


@login_required
def vote_receipt(request, election_id):
    try:
        voter = Voter.objects.get(user=request.user)
    except Voter.DoesNotExist:
        return redirect('voter_login')

    election = get_object_or_404(Election, id=election_id)

    # Get all votes cast by this voter in this election
    votes = Vote.objects.filter(
        voter=voter,
        election=election
    ).select_related('position').order_by('position__name')

    if not votes.exists():
        messages.warning(request, "No votes found for this election.")
        return redirect('vote_dashboard')

    return render(request, "core/vote_receipt.html", {
        "voter": voter,
        "election": election,
        "votes": votes,
    })


def candidate_profile(request, candidate_id):
    candidate = get_object_or_404(
        Candidate.objects.select_related(
            'user', 'position', 'election', 'election__institution'
        ),
        id=candidate_id
    )

    # Vote count for this candidate
    vote_count = Vote.objects.filter(candidate=candidate).count()

    # Total votes for this position (to calculate percentage)
    position_total = Vote.objects.filter(
        position=candidate.position
    ).count()

    percentage = (
        round((vote_count / position_total) * 100, 1)
        if position_total > 0 else 0
    )

    # Other candidates in same position (for comparison)
    other_candidates = Candidate.objects.filter(
        position=candidate.position
    ).exclude(id=candidate.id).select_related('user')

    context = {
        'candidate': candidate,
        'vote_count': vote_count,
        'percentage': percentage,
        'other_candidates': other_candidates,
        'election': candidate.election,
        'show_results': candidate.election.is_closed or request.user.is_staff,
    }

    return render(request, 'core/candidate_profile.html', context)



@login_required
def download_vote_receipt(request, election_id):
    try:
        voter = Voter.objects.get(user=request.user)
    except Voter.DoesNotExist:
        return redirect('voter_login')

    election = get_object_or_404(Election, id=election_id)

    votes = Vote.objects.filter(
        voter=voter,
        election=election
    ).select_related('position', 'candidate__user').order_by('position__name')

    if not votes.exists():
        messages.warning(request, "No votes found for this election.")
        return redirect('vote_dashboard')

    # Colors
    DARK_GREEN = colors.HexColor('#1B5E20')
    GOLD       = colors.HexColor('#C9A84C')
    LIGHT_GREEN = colors.HexColor('#E8F5E9')
    MID_GREY   = colors.HexColor('#888888')
    DARK_GREY  = colors.HexColor('#2c2c2c')
    WHITE      = colors.white

    # PDF response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="VoteReceipt_{voter.user.username}_{election.title}.pdf"'
    )

    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=50,
        leftMargin=50,
        topMargin=70,
        bottomMargin=70,
    )

    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Normal'],
        fontSize=20,
        fontName='Helvetica-Bold',
        textColor=DARK_GREEN,
        alignment=1,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=11,
        fontName='Helvetica',
        textColor=MID_GREY,
        alignment=1,
        spaceAfter=4,
    )
    center_style = ParagraphStyle(
        'Center',
        parent=styles['Normal'],
        fontSize=10,
        alignment=1,
        spaceAfter=4,
        textColor=DARK_GREY,
    )
    section_style = ParagraphStyle(
        'Section',
        parent=styles['Normal'],
        fontSize=11,
        fontName='Helvetica-Bold',
        textColor=WHITE,
        leftIndent=8,
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontSize=10,
        leading=16,
        alignment=1,
        textColor=DARK_GREY,
    )

    # ─────────────────────────────────────
    # LOGO — ReportLab only supports PNG/JPG, not SVG
    # ─────────────────────────────────────
    for logo_filename in ['logo.png', 'logo.jpg', 'institution_logo.png']:
        logo_path = os.path.join(settings.BASE_DIR, "static", "images", logo_filename)
        if os.path.exists(logo_path):
            try:
                logo = Image(logo_path, width=80, height=80)
                logo.hAlign = "CENTER"
                elements.append(logo)
                elements.append(Spacer(1, 8))
                break
            except Exception:
                continue

    # ─────────────────────────────────────
    # HEADER
    # ─────────────────────────────────────
    elements.append(Paragraph("EduVoteGH", title_style))
    elements.append(Paragraph("Official Vote Receipt", subtitle_style))
    elements.append(Spacer(1, 6))

    # Gold divider
    divider = Table([['']], colWidths=[6.3*inch])
    divider.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
        ('LINEABOVE', (0, 0), (-1, -1), 0.5, DARK_GREEN),
    ]))
    elements.append(divider)
    elements.append(Spacer(1, 16))

    # ─────────────────────────────────────
    # VOTER INFO
    # ─────────────────────────────────────
    voter_header = Table(
        [[Paragraph('VOTER INFORMATION', section_style)]],
        colWidths=[6.3*inch]
    )
    voter_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_GREEN),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
    ]))
    elements.append(voter_header)

    voter_info = [
        [Paragraph('<b>Voter Name:</b>', styles['Normal']),
         Paragraph(voter.user.get_full_name() or voter.user.username, styles['Normal'])],
        [Paragraph('<b>Username:</b>', styles['Normal']),
         Paragraph(voter.user.username, styles['Normal'])],
        [Paragraph('<b>Institution:</b>', styles['Normal']),
         Paragraph(election.institution.name, styles['Normal'])],
        [Paragraph('<b>Election:</b>', styles['Normal']),
         Paragraph(election.title, styles['Normal'])],
        [Paragraph('<b>Date of Vote:</b>', styles['Normal']),
         Paragraph(votes.first().timestamp.strftime('%B %d, %Y at %I:%M %p'), styles['Normal'])],
    ]
    voter_table = Table(voter_info, colWidths=[2*inch, 4.3*inch])
    voter_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GREEN),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [WHITE, LIGHT_GREEN]),
        ('BOX', (0, 0), (-1, -1), 0.5, DARK_GREEN),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (0, -1), 12),
    ]))
    elements.append(voter_table)
    elements.append(Spacer(1, 24))

    # ─────────────────────────────────────
    # RECEIPT CODES PER POSITION
    # ─────────────────────────────────────
    receipt_header = Table(
        [[Paragraph('VOTE RECEIPT CODES', section_style)]],
        colWidths=[6.3*inch]
    )
    receipt_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_GREEN),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
    ]))
    elements.append(receipt_header)

    receipt_data = [[
        Paragraph('<b>Position</b>', styles['Normal']),
        Paragraph('<b>Receipt Code</b>', styles['Normal']),
        Paragraph('<b>Time Cast</b>', styles['Normal']),
    ]]

    for vote in votes:
        receipt_data.append([
            Paragraph(vote.position.name, styles['Normal']),
            Paragraph(
                f'<b><font color="#1B5E20" size="13">{vote.receipt_code}</font></b>',
                styles['Normal']
            ),
            Paragraph(
                vote.timestamp.strftime('%I:%M %p'),
                styles['Normal']
            ),
        ])

    receipt_table = Table(
        receipt_data,
        colWidths=[2.5*inch, 2.3*inch, 1.5*inch]
    )
    receipt_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), DARK_GREY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GREEN]),
        ('BOX', (0, 0), (-1, -1), 0.5, DARK_GREEN),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 10),
        ('LEFTPADDING', (0, 1), (0, -1), 10),
    ]))
    elements.append(receipt_table)
    elements.append(Spacer(1, 24))

    # ─────────────────────────────────────
    # VERIFICATION NOTE
    # ─────────────────────────────────────
    elements.append(Paragraph(
        "Use the receipt codes above to verify your vote at "
        "<b>eduvotegh.com/verify/</b> — "
        "Your candidate choice is kept private to protect vote secrecy.",
        body_style
    ))
    elements.append(Spacer(1, 30))

    # ─────────────────────────────────────
    # QR CODE — links to verify page
    # ─────────────────────────────────────
    receipt_codes = " | ".join([v.receipt_code for v in votes])
    qr_data = (
        f"EduVoteGH Vote Receipt | "
        f"Voter: {voter.user.username} | "
        f"Election: {election.title} | "
        f"Codes: {receipt_codes}"
    )

    qr_code = qr.QrCodeWidget(qr_data)
    bounds = qr_code.getBounds()
    qr_w = bounds[2] - bounds[0]
    qr_h = bounds[3] - bounds[1]
    qr_drawing = Drawing(
        110, 110,
        transform=[110./qr_w, 0, 0, 110./qr_h, 0, 0]
    )
    qr_drawing.add(qr_code)

    elements.append(Paragraph("<b>Scan to Verify</b>", center_style))
    elements.append(Spacer(1, 6))
    qr_table = Table([[qr_drawing]], colWidths=[6.3*inch])
    qr_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(qr_table)

    # ─────────────────────────────────────
    # WATERMARK + BORDER
    # ─────────────────────────────────────
    def add_watermark(canvas_obj, doc):
        canvas_obj.saveState()

        # Double border
        canvas_obj.setLineWidth(3)
        canvas_obj.setStrokeColor(DARK_GREEN)
        canvas_obj.rect(20, 20, A4[0]-40, A4[1]-40)
        canvas_obj.setLineWidth(1)
        canvas_obj.setStrokeColor(GOLD)
        canvas_obj.rect(26, 26, A4[0]-52, A4[1]-52)

        # Watermark
        canvas_obj.setFont("Helvetica-Bold", 60)
        canvas_obj.setFillColor(DARK_GREEN)
        canvas_obj.setFillAlpha(0.05)
        canvas_obj.translate(A4[0]/2, A4[1]/2)
        canvas_obj.rotate(45)
        canvas_obj.drawCentredString(0, 0, "EDUVOTEGH")
        canvas_obj.rotate(-45)
        canvas_obj.translate(-A4[0]/2, -A4[1]/2)

        canvas_obj.restoreState()

        # Footer
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(MID_GREY)
        canvas_obj.drawCentredString(
            A4[0]/2, 12,
            f"EduVoteGH Official Vote Receipt  |  "
            f"{voter.user.username}  |  "
            f"Page {doc.page}"
        )

    doc.build(
        elements,
        onFirstPage=add_watermark,
        onLaterPages=add_watermark
    )

    return response

