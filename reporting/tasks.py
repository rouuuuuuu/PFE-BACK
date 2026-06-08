from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from io import BytesIO
from django.core.files.base import ContentFile

# Imports ReportLab pour la mise en page et les tableaux
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Import de tes modèles
from .models import MonthlyReport
from devices.models import Router, Switch
from backhaul.models import BackhaulLink
from provisioning.models import ProvisioningTask

@shared_task
def generate_monthly_network_report():
    today = timezone.now()
    first_of_this_month = today.replace(day=1)
    last_month_date = first_of_this_month - timedelta(days=1)
    month_str = last_month_date.strftime("%B %Y") 

    if MonthlyReport.objects.filter(month_year=month_str).exists():
        return f"Report for {month_str} already exists."

    # 1. Métriques Globales
    t_routers = Router.objects.count()
    t_switches = Switch.objects.count()
    t_links = BackhaulLink.objects.count()
    c_alarms = BackhaulLink.objects.filter(alarm_severity='critical').count()

    # 2. Récupération des tâches exécutées le mois dernier
    tasks_last_month = ProvisioningTask.objects.filter(
        created_at__year=last_month_date.year, 
        created_at__month=last_month_date.month
    )
    
    completed_tasks = tasks_last_month.filter(status='completed').count()
    failed_tasks = tasks_last_month.filter(status='failed').count()

    # 3. Création de l'enregistrement en Base de Données
    report = MonthlyReport.objects.create(
        month_year=month_str,
        total_routers=t_routers,
        total_switches=t_switches,
        total_links=t_links,
        critical_alarms_count=c_alarms,
        provisioning_tasks_completed=completed_tasks,
        provisioning_tasks_failed=failed_tasks
    )

    # 4. GÉNÉRATION DU PDF AVEC TABLEAU DETAILLÉ
    buffer = BytesIO()
    
    # On utilise SimpleDocTemplate pour gérer automatiquement les sauts de page si la liste est longue
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4,
        rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    story = []

    # Styles personnalisés
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Heading1'],
        fontSize=22,
        textColor=colors.darkorange,
        spaceAfter=10
    )
    subtitle_style = ParagraphStyle(
        'ReportSubtitle',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.gray,
        spaceAfter=20
    )
    heading_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor("#1a1a1a"),
        spaceBefore=15,
        spaceAfter=10
    )

    # Entête du document
    story.append(Paragraph("Rapport Mensuel d'Activité NOC", title_style))
    story.append(Paragraph(f"Période : {month_str} | Généré le : {today.strftime('%d/%m/%Y à %H:%M')}", subtitle_style))
    story.append(Spacer(1, 10))

    # Section 1 : Synthèse Globale
    story.append(Paragraph("1. Synthèse de l'Infrastructure & Activité", heading_style))
    summary_data = [
        ["Routeurs Actifs", str(t_routers), "Alarmes Critiques", str(c_alarms)],
        ["Commutateurs Actifs", str(t_switches), "Tâches Réussies", str(completed_tasks)],
        ["Liens Backhaul", str(t_links), "Tâches Échouées", str(failed_tasks)]
    ]
    summary_table = Table(summary_data, colWidths=[130, 80, 150, 80])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor("#f8f9fa")),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor("#f8f9fa")),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.HexColor("#333333")),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#dee2e6")),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Section 2 : Tableau détaillé des tâches exécutées
    story.append(Paragraph("2. Historique Détaillé des Tâches de Provisioning", heading_style))
    
    # Construction des lignes du tableau
    # Entête du tableau
    table_data = [["Date", "Client / Réf", "Type de Tâche", "Équipement", "Statut"]]
    
    # On boucle sur les vraies tâches du mois dernier
    for task in tasks_last_month:
        # Formatage de la date de création
        date_str = task.created_at.strftime("%d/%m/%Y")
        
        # Récupération sécurisée des attributs (ajuste selon tes vrais champs de ProvisioningTask)
        client = getattr(task, 'client_name', 'N/A') or getattr(task, 'project_reference', 'N/A')
        task_type = task.get_task_type_display() if hasattr(task, 'get_task_type_display') else task.task_type
        node = getattr(task, 'target_node', 'N/A')
        status = "RÉUSSI" if task.status == 'completed' else "ÉCHOUÉ"
        
        table_data.append([date_str, client, task_type, node, status])

    # Si aucune tâche n'a été exécutée le mois dernier
    if len(table_data) == 1:
        table_data.append(["-", "Aucune activité enregistrée", "-", "-", "-"])

    # Configuration des largeurs de colonnes pour que ça rentre parfaitement dans la page A4
    task_table = Table(table_data, colWidths=[70, 130, 130, 110, 70])
    
    # Style du tableau (Orange pour l'entête, lignes alternées grises)
    t_style = TableStyle([
        # Style de l'entête
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#ff6600")), # Orange Orange Tunisia
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        
        # Style du corps
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
        ('ALIGN', (4, 1), (4, -1), 'CENTER'), # Centre le Statut
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#dee2e6")),
    ])
    
    # Coloration dynamique du statut (Vert si Réussi, Rouge si Échoué)
    for i in range(1, len(table_data)):
        if table_data[i][4] == "RÉUSSI":
            t_style.add('TEXTCOLOR', (4, i), (4, i), colors.green)
        elif table_data[i][4] == "ÉCHOUÉ":
            t_style.add('TEXTCOLOR', (4, i), (4, i), colors.red)
            
        # Alternance des couleurs de lignes pour la lisibilité
        if i % 2 == 0:
            t_style.add('BACKGROUND', (0, i), (-1, i), colors.HexColor("#f8f9fa"))

    task_table.setStyle(t_style)
    story.append(task_table)

    # Génération finale du PDF
    doc.build(story)
    
    # 5. Sauvegarde du fichier dans le modèle Django
    pdf_data = buffer.getvalue()
    buffer.close()
    
    file_name = f"NOC_Report_{month_str.replace(' ', '_')}.pdf"
    report.pdf_file.save(file_name, ContentFile(pdf_data))

    return f"Successfully generated detailed report with tasks history for {month_str} (ID: {report.id})"
