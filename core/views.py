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

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from datetime import datetime
from reportlab.platypus import PageBreak
from reportlab.lib.units import inch
from django.http import HttpResponseForbidden
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
import uuid

import os
from django.contrib.auth.hashers import make_password

from .models import ContactMessage
from django.core.mail import send_mail
from django.conf import settings
from reportlab.platypus import Image
from reportlab.graphics.shapes import Circle

from reportlab.graphics.shapes import Circle, String

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

    # 🔒 Restrict to superusers only
    if not request.user.is_superuser:
        return HttpResponseForbidden("Access Denied")

    now = timezone.now()
    election = get_object_or_404(Election, id=election_id)

    results = []

    positions = Position.objects.filter(election=election)

    # ✅ TOTAL VOTES CAST IN ELECTION
    total_votes_cast = Vote.objects.filter(position__election=election).count()

    for position in positions:

        candidates = Candidate.objects.filter(position=position)

        # ✅ TOTAL VOTES FOR THIS POSITION
        position_total_votes = Vote.objects.filter(position=position).count()

        candidate_list = []

        for candidate in candidates:

            votes_count = Vote.objects.filter(candidate=candidate).count()

            # attach vote count
            candidate.votes_count = votes_count

            # ✅ calculate percentage correctly
            if position_total_votes > 0:
                candidate.percentage = round((votes_count / position_total_votes) * 100, 1)
            else:
                candidate.percentage = 0

            candidate_list.append(candidate)

        winner = None
        is_tie = False

        if candidate_list:

            max_votes = max(c.votes_count for c in candidate_list)

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
        'total_votes_cast': total_votes_cast,  # ✅ NEW
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
                df = pd.read_excel(file, engine="openpyxl")
            except Exception:
                messages.error(request, "Invalid Excel file. Upload a valid .xlsx file.")
                return render(request, 'core/upload_voters.html', {'form': form})

            required_columns = ['full_name', 'phone', 'username']

            for col in required_columns:
                if col not in df.columns:
                    messages.error(request, f"Missing column: {col}")
                    return render(request, 'core/upload_voters.html', {'form': form})

            institution = Institution.objects.first()
            election = Election.objects.first()

            users_to_create = []
            voters_to_create = []
            credentials = []

            # Get existing usernames (duplicate protection)
            existing_usernames = set(
                User.objects.values_list('username', flat=True)
            )

            for _, row in df.iterrows():

                full_name = str(row['full_name']).strip()
                phone = str(row['phone']).strip()
                username = str(row['username']).strip()

                if not username or username in existing_usernames:
                    continue

                password = ''.join(random.choices(string.digits, k=8))

                user = User(
                    username=username,
                    first_name=full_name,
                    password=make_password(password)
                )

                users_to_create.append(user)

                credentials.append({
                    "full_name": full_name,
                    "username": username,
                    "phone": phone,
                    "password": password
                })

                existing_usernames.add(username)

            # Bulk create users
            User.objects.bulk_create(users_to_create)

            # Fetch created users
            users = User.objects.filter(
                username__in=[c["username"] for c in credentials]
            )

            user_map = {user.username: user for user in users}

            for cred in credentials:
                voter = Voter(
                    user=user_map[cred["username"]],
                    institution=institution,
                    phone=cred["phone"]
                )
                voters_to_create.append(voter)

            # Bulk create voters
            Voter.objects.bulk_create(voters_to_create)

            # Assign election
            voters = Voter.objects.filter(
                user__username__in=[c["username"] for c in credentials]
            )

            for voter in voters:
                voter.elections.add(election)

            # Generate CSV download
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="voter_credentials.csv"'

            writer = csv.writer(response)
            writer.writerow(['full_name', 'username', 'phone', 'password'])

            for cred in credentials:
                writer.writerow([
                    cred['full_name'],
                    cred['username'],
                    cred['phone'],
                    cred['password']
                ])

            return response

    else:
        form = VoterCSVUploadForm()

    return render(request, 'core/upload_voters.html', {'form': form})


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

    # -------------------------------------------------
    # CALCULATIONS
    # -------------------------------------------------
    total_registered_voters = Voter.objects.filter(
        elections=election
    ).count()

    total_votes_cast = Vote.objects.filter(
        election=election
    ).count()

    turnout_percentage = (
        round((total_votes_cast / total_registered_voters) * 100, 2)
        if total_registered_voters > 0 else 0
    )

    verification_id = str(uuid.uuid4()).split("-")[0].upper()

    verification_data = f"""
Election: {election.title}
Reference: {verification_id}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""

    # -------------------------------------------------
    # PDF RESPONSE
    # -------------------------------------------------
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="{election.title}_Official_Results.pdf"'
    )

    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=60,
        bottomMargin=60,
    )

    elements = []
    styles = getSampleStyleSheet()

    # Custom centered style
    from reportlab.lib.styles import ParagraphStyle
    centered = ParagraphStyle(
        name="Centered",
        parent=styles["Normal"],
        alignment=1,
        fontSize=12,
        spaceAfter=6,
    )

    ##logo_path = os.path.join(settings.BASE_DIR, "static", "images", "institution_logo.png")

    try:
        logo = Image(logo_path, width=80, height=80)
        logo.hAlign = "CENTER"
        elements.append(logo)
        elements.append(Spacer(1,10))
    except:
        pass


    # -------------------------------------------------
    # HEADER
    # -------------------------------------------------

    elements.append(Paragraph(
        f"<b>{election.institution.name.upper()}</b>",
        styles["Title"]
    ))

    elements.append(Spacer(1,6))

    elements.append(Paragraph(
        "<b>EduVoteGH</b>",
        centered
    ))

    elements.append(Spacer(1,6))

    elements.append(Paragraph(
        "<b>OFFICIAL CERTIFICATE OF DECLARATION OF RESULTS</b>",
        centered
    ))

    elements.append(Spacer(1,20))

    elements.append(Spacer(1, 20))

    elements.append(Paragraph(
        f"<b>Election:</b> {election.title}",
        styles["Normal"]
    ))

    elements.append(Paragraph(
        f"<b>Date of Issue:</b> {datetime.now().strftime('%B %d, %Y %H:%M')}",
        styles["Normal"]
    ))

    elements.append(Spacer(1, 25))

    # -------------------------------------------------
    # SUMMARY SECTION (BOXED)
    # -------------------------------------------------
    summary_data = [
        ["TOTAL REGISTERED VOTERS", total_registered_voters],
        ["TOTAL VALID VOTES CAST", total_votes_cast],
        ["VOTER TURNOUT", f"{turnout_percentage}%"],
    ]

    summary_table = Table(summary_data, colWidths=[3.5*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.lightgrey),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
    ]))

    elements.append(Paragraph("<b>OFFICIAL SUMMARY</b>", styles["Heading2"]))
    elements.append(Spacer(1, 10))
    elements.append(summary_table)
    elements.append(Spacer(1, 30))

    # -------------------------------------------------
    # RESULTS SECTION
    # -------------------------------------------------
    for position in positions:

        elements.append(Paragraph(
            f"<b>POSITION: {position.name.upper()}</b>",
            styles["Heading3"]
        ))
        elements.append(Spacer(1, 10))

        candidates = Candidate.objects.filter(
            position=position
        ).annotate(
            total_votes=Count('vote')
        ).order_by('-total_votes')

        if not candidates.exists():
            elements.append(Paragraph(
                "No candidates available.",
                styles["Normal"]
            ))
            elements.append(Spacer(1, 20))
            continue

        total_votes = sum(c.total_votes for c in candidates)

        table_data = [["Candidate", "Votes", "Percentage"]]

        for index, candidate in enumerate(candidates):
            percentage = (
                round((candidate.total_votes / total_votes) * 100, 2)
                if total_votes > 0 else 0
            )

            name = candidate.user.get_full_name() or candidate.user.username

            if index == 0 and candidate.total_votes > 0:
                name = f"{name} (DECLARED WINNER)"

            table_data.append([
                name,
                candidate.total_votes,
                f"{percentage}%"
            ])

        results_table = Table(table_data, colWidths=[3*inch, 1.2*inch, 1.2*inch])

        results_table.setStyle(TableStyle([

        ('BACKGROUND',(0,0),(-1,0),colors.darkblue),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),

        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),

        ('GRID',(0,0),(-1,-1),0.5,colors.grey),

        ('ALIGN',(1,1),(-1,-1),'CENTER'),

        ('ROWBACKGROUNDS',
        (0,1),(-1,-1),
        [colors.whitesmoke,colors.lightgrey]),

        ('BOTTOMPADDING',(0,0),(-1,0),10),
        ('TOPPADDING',(0,0),(-1,0),10),

        ]))

        elements.append(results_table)
        elements.append(Spacer(1, 25))

    # -------------------------------------------------
    # CERTIFICATION DECLARATION
    # -------------------------------------------------
    elements.append(Spacer(1, 15))
    elements.append(Paragraph(
        "<b>CERTIFICATION</b>",
        styles["Heading2"]
    ))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "This is to certify that the foregoing results "
        "constitute the official and final declaration "
        "of the election conducted under the authority "
        "of the EduVoteGH Electoral Commission.",
        styles["Normal"]
    ))

    elements.append(Spacer(1, 15))

    elements.append(Paragraph(
        f"<b>Reference Number:</b> {verification_id}",
        styles["Normal"]
    ))

    elements.append(Spacer(1, 40))

    elements.append(Paragraph(
        "_______________________________",
        styles["Normal"]
    ))
    elements.append(Paragraph(
        "Returning Officer",
        styles["Normal"]
    ))

    from reportlab.graphics.shapes import Drawing

    seal = Drawing(120,120)

    seal.add(Circle(60,60,50))
    seal.add(Circle(60,60,45))

    seal.add(String(
        60,
        60,
        "OFFICIAL\nSEAL",
        textAnchor="middle"
    ))

    elements.append(Spacer(1,10))
    elements.append(seal)

    elements.append(Spacer(1, 25))

    # -------------------------------------------------
    # QR CODE SECTION
    # -------------------------------------------------
    qr_code = qr.QrCodeWidget(verification_data)
    bounds = qr_code.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]

    drawing = Drawing(100, 100,
                      transform=[100./width, 0, 0, 100./height, 0, 0])
    drawing.add(qr_code)

    elements.append(Paragraph("<b>Scan for Verification</b>", centered))
    elements.append(Spacer(1, 8))
    elements.append(drawing)

    # -------------------------------------------------
    # WATERMARK + FOOTER
    # -------------------------------------------------
    def add_watermark(canvas_obj, doc):

        canvas_obj.saveState()

        # Certificate Border
        canvas_obj.setLineWidth(3)
        canvas_obj.rect(
            25, 25,
            A4[0] - 50,
            A4[1] - 50
        )

        # Inner Border
        canvas_obj.setLineWidth(1)
        canvas_obj.rect(
            35, 35,
            A4[0] - 70,
            A4[1] - 70
        )

        # Watermark
        canvas_obj.setFont("Helvetica-Bold", 70)
        canvas_obj.setFillGray(0.9)
        canvas_obj.translate(300, 450)
        canvas_obj.rotate(45)
        canvas_obj.drawCentredString(0, 0, "EDUVOTE")

        canvas_obj.restoreState()

        # Footer
        canvas_obj.setFont("Helvetica", 9)
        canvas_obj.drawRightString(
            A4[0] - 40,
            20,
            f"Page {doc.page}"
        )

    doc.build(
        elements,
        onFirstPage=add_watermark,
        onLaterPages=add_watermark
    )

    return response


def contact(request):

    if request.method == "POST":

        name = request.POST.get("name")
        email = request.POST.get("email")
        message = request.POST.get("message")

        # Save to database
        ContactMessage.objects.create(
            name=name,
            email=email,
            message=message
        )

        # Send email notification
        send_mail(
            subject=f"New EduVoteGH Contact Message from {name}",
            message=f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=["eduvote.gh@gmail.com"],
            fail_silently=True
        )

        return render(request, "core/contact.html", {"success": True})

    return render(request, "core/contact.html")


def contact_view(request):

    success = False

    if request.method == "POST":

        name = request.POST.get("name")
        email = request.POST.get("email")
        message = request.POST.get("message")

        ContactMessage.objects.create(
            name=name,
            email=email,
            message=message
        )

        success = True

    return render(request, "core/contact.html", {"success": success})


def home(request):
    return render(request, 'core/home.html')

from django.http import JsonResponse
from .models import Election

def cast_vote(request):
    if request.method == "POST":
        election = Election.objects.first()  # or get current election

        # BLOCK voting if election inactive
        if not election.is_active:
            return JsonResponse({
                "success": False,
                "message": "Voting is currently closed."
            }, status=403)

        # Continue with voting logic
        # save vote here

        return JsonResponse({
            "success": True,
            "message": "Vote cast successfully."
        })