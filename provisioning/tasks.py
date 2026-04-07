import redis
from celery import shared_task
from django.utils import timezone

redis_client = redis.Redis(host='localhost', port=6379, db=0)

@shared_task(bind=True)
def run_provisioning(self, task_id):
    from provisioning.models import ProvisioningTask
    
    task = ProvisioningTask.objects.get(id=task_id)
    device_ip = task.device_ip
    lock_key = f'device_lock_{device_ip}'

    # ── Acquire device lock (no overlap) ──────────────────────
    lock_acquired = redis_client.set(
        lock_key,
        task_id,
        nx=True,   # only set if not exists
        ex=600     # auto expire after 10 minutes
    )

    if not lock_acquired:
        # Device is busy — retry in 2 minutes
        task.status = 'queued'
        task.result = f'Device {device_ip} is busy. Retrying...'
        task.save()
        raise self.retry(countdown=120, max_retries=5)

    try:
        # ── Mark as running ───────────────────────────────────
        task.status = 'running'
        task.started_at = timezone.now()
        task.save()

        # ── Netmiko placeholder ───────────────────────────────
        # TODO: supervisor will provide CLI commands
        # from netmiko import ConnectHandler
        # device = {
        #     'device_type': 'huawei',
        #     'host': task.device_ip,
        #     'username': 'admin',
        #     'password': 'password',
        # }
        # with ConnectHandler(**device) as conn:
        #     output = conn.send_config_set(commands)

        # Simulate success for now
        import time
        time.sleep(2)  # simulate provisioning time

        # ── Mark as completed ─────────────────────────────────
        task.status = 'completed'
        task.result = f'✅ Provisioning completed for {task.device_name} ({task.task_type})'
        task.completed_at = timezone.now()
        task.save()

    except Exception as e:
        task.status = 'failed'
        task.result = f'❌ Error: {str(e)}'
        task.completed_at = timezone.now()
        task.save()

    finally:
        # ── Always release device lock ────────────────────────
        redis_client.delete(lock_key)

    return task.result
