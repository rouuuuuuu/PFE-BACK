from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import MonthlyReport
from .serializers import MonthlyReportSerializer
from .tasks import generate_monthly_network_report  # Importe ta fonction !

class MonthlyReportViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Lecture des rapports et génération manuelle.
    """
    queryset = MonthlyReport.objects.all()
    serializer_class = MonthlyReportSerializer

    @action(detail=False, methods=['post'])
    def generate_now(self, request):
        """
        Bouton manuel : POST /api/reports/monthly/generate_now/
        """
        try:
            # Pour un test manuel immédiat, on l'appelle directement de façon synchrone
            # (Si tu voulais le faire en arrière-plan, tu ferais generate_monthly_network_report.delay())
            result_message = generate_monthly_network_report()
            
            return Response({"status": "success", "message": result_message})
        except Exception as e:
            return Response({"status": "error", "message": str(e)}, status=500)
