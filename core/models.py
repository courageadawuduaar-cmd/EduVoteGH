from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from cloudinary.models import CloudinaryField
import uuid


class Institution(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    logo = CloudinaryField('image', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Election(models.Model):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_active = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.institution.name}"

    # 🔒 ENTERPRISE: Auto-close if end_time has passed
    def save(self, *args, **kwargs):
        if self.end_time <= timezone.now():
            self.is_active = False
        super().save(*args, **kwargs)

    # ✅ Property: Is election currently running?
    @property
    def is_live(self):
        now = timezone.now()
        return self.start_time <= now <= self.end_time and self.is_active

    @property
    def timeline_progress(self):
        now = timezone.now()

        if now <= self.start_time:
            return 0

        if now >= self.end_time:
            return 100

        total = (self.end_time - self.start_time).total_seconds()
        passed = (now - self.start_time).total_seconds()

        return round((passed / total) * 100)

    # ✅ Property: Is election closed?
    @property
    def is_closed(self):
        return timezone.now() > self.end_time

    # 📊 Turnout percentage
    def turnout_percentage(self):
        total_voters = self.voters.count()
        voted = Vote.objects.filter(election=self).values('voter').distinct().count()
        if total_voters == 0:
            return 0
        return round((voted / total_voters) * 100, 1)


class Position(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.election.title})"


class Candidate(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    position = models.ForeignKey(Position, on_delete=models.CASCADE)
    election = models.ForeignKey(Election, on_delete=models.CASCADE)
    manifesto = models.TextField(blank=True)
    photo = CloudinaryField('image', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'position', 'election')

    def __str__(self):
        return f"{self.user.username} for {self.position.name}"


class Voter(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE)

    elections = models.ManyToManyField(
        'Election',
        related_name='voters',
        blank=True
    )

    phone = models.CharField(max_length=20, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username


class Vote(models.Model):
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, db_index=True)
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, db_index=True)
    position = models.ForeignKey(Position, on_delete=models.CASCADE, db_index=True)
    election = models.ForeignKey(Election, on_delete=models.CASCADE, db_index=True)

    receipt_code = models.CharField(max_length=20, unique=True, editable=False, null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('voter', 'position', 'election')
        indexes = [
            models.Index(fields=["election"]),
            models.Index(fields=["position"]),
            models.Index(fields=["voter"]),
            models.Index(fields=["candidate"]),
        ]

    def save(self, *args, **kwargs):
        if not self.receipt_code:
            self.receipt_code = f"EVGH-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.voter.user.username} voted for {self.candidate.user.username} ({self.position.name})"


# 📁 ENTERPRISE: Admin Audit Logs
class AdminAuditLog(models.Model):
    admin = models.ForeignKey(User, on_delete=models.CASCADE)
    action = models.CharField(max_length=255)
    election = models.ForeignKey(Election, on_delete=models.CASCADE, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.admin.username} - {self.action} - {self.timestamp}"

class ContactMessage(models.Model):

    name = models.CharField(max_length=100)

    school = models.CharField(max_length=200)

    role = models.CharField(max_length=100)

    phone = models.CharField(max_length=20)

    email = models.EmailField()

    students = models.IntegerField(blank=True, null=True)

    message = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    is_replied = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} - {self.school}"


class ActivityLog(models.Model):

    action = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.action



