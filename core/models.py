from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Institution(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Election(models.Model):
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_active = models.BooleanField(default=False)
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

    # ✅ Property: Is election closed?
    @property
    def is_closed(self):
        return timezone.now() > self.end_time

    # 📊 Turnout percentage
    def turnout_percentage(self):
        total_voters = self.voter_set.count()
        total_votes = Vote.objects.filter(election=self).values('voter').distinct().count()

        if total_voters == 0:
            return 0

        return round((total_votes / total_voters) * 100, 2)


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
    photo = models.ImageField(upload_to='candidates/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} for {self.position.name}"


class Voter(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE)
    elections = models.ManyToManyField(Election)
    created_at = models.DateTimeField(auto_now_add=True)
    phone = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} ({self.institution.name})"


class Vote(models.Model):
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE)
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE)
    position = models.ForeignKey(Position, on_delete=models.CASCADE)
    election = models.ForeignKey(Election, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('voter', 'position', 'election')

    def __str__(self):
        return f"{self.voter.user.username} voted for {self.candidate.user.username} ({self.position.name})"


# 📁 ENTERPRISE: Admin Audit Logs
class AdminAuditLog(models.Model):
    admin = models.ForeignKey(User, on_delete=models.CASCADE)
    action = models.CharField(max_length=255)
    election = models.ForeignKey(Election, on_delete=models.CASCADE, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.admin.username} - {self.action} - {self.timestamp}"