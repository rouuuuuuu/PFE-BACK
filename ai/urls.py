from django.urls import path
from . import views

app_name = "ai_assistant"

urlpatterns = [
    # On garde l'endpoint des statistiques du tableau de bord
    path("stats/", views.ai_stats, name="stats"),

    # On utilise uniquement le chat local "From Scratch"
    path("chat/native/", views.ai_chat_local_scratch, name="chat_native"),
]
