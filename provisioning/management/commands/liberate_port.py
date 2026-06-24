# provisioning/management/commands/liberate_port.py

from django.core.management.base import BaseCommand
from provisioning.tasks import execute_port_liberation
from provisioning.models import ProvisioningTask
from devices.models import Router, Port


class Command(BaseCommand):
    help = 'Liberate a provisioned port for re-testing (resets device + DB)'

    def add_arguments(self, parser):
        parser.add_argument('task_id',   type=int, help='ProvisioningTask ID')
        parser.add_argument('router_id', type=int, help='Router ID')
        parser.add_argument('port_id',   type=int, help='Port ID')

    def handle(self, *args, **options):
        task_id   = options['task_id']
        router_id = options['router_id']
        port_id   = options['port_id']

        self.stdout.write(f'Liberating task={task_id} router={router_id} port={port_id}...')

        job = execute_port_liberation.delay(task_id, router_id, port_id)
        self.stdout.write(self.style.SUCCESS(f'Queued — Celery job ID: {job.id}'))
