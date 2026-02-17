from django.contrib import admin
from .models import Institution
from .models import Institution, Election
from .models import Institution, Election, Position
from .models import Institution, Election, Position, Candidate
from .models import Voter
from .models import Vote

admin.site.register(Institution)
admin.site.register(Election)
admin.site.register(Position)
admin.site.register(Candidate)
admin.site.register(Voter)
admin.site.register(Vote)