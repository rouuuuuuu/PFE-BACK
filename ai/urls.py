from django.urls import path
from . import views

urlpatterns = [
    path('alarm-analysis/', views.alarm_analysis_view),
    path('nlp-provisioning/', views.nlp_provisioning_view),
]
