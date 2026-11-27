from celery import shared_task
from celery.signals import worker_process_init

from . import services


@worker_process_init.connect
def _warm_up(**_):
    services.analyzer()                         # load analyzer khi worker khởi động, không phải ở job đầu tiên


@shared_task(name="jobs.tasks.process_job", acks_late=True)
def process_job_task(job_id: str):
    services.process_job(job_id)


@shared_task(name="jobs.tasks.render_job", acks_late=True)
def render_job_task(job_id: str):
    services.render_job(job_id)


@shared_task(name="jobs.tasks.cleanup_expired")
def cleanup_expired():
    return services.cleanup_expired()
