"""企业合规执行中心 Celery 应用。"""

from celery import Celery

from app.config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND


celery_app = Celery(
    "enterprise_compliance_center",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["app.worker"],
)
celery_app.conf.update(
    task_default_queue="agent_tasks",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "agent-worker-heartbeat": {
            "task": "agent.worker_heartbeat",
            "schedule": 5.0,
        },
        "recover-agent-outbox": {
            "task": "agent.dispatch_pending_outbox",
            "schedule": 15.0,
        },
        "recover-agent-evaluations": {
            "task": "agent.dispatch_pending_evaluations",
            "schedule": 15.0,
        },
    },
)
