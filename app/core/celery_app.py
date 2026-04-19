from celery import Celery

from app.core.config import get_settings


settings = get_settings()

celery_app = Celery(
    "reporting_service",
    broker=settings.effective_celery_broker_url,
    backend=settings.effective_celery_result_backend,
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    worker_hijack_root_logger=False,
    task_time_limit=settings.task_time_limit_seconds,
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    task_always_eager=settings.task_always_eager,
)
celery_app.autodiscover_tasks(["app.tasks"])
