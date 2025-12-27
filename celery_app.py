# celery_app.py - Настройка Celery для очередей уведомлений
# Использует Redis как брокер сообщений

import os
import sys
# Ensure project root is importable so celery autodiscover can find local modules like `tasks`
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from celery import Celery
from config import REDIS_URL, TIMEZONE

celery = Celery(
    "poputchik_bot",
    broker=REDIS_URL,
    backend=REDIS_URL.replace("/0", "/1")  # Используем другую БД для результатов
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 минут максимум на задачу
    worker_prefetch_multiplier=1,  # Для равномерного распределения задач
    task_acks_late=True,  # Подтверждать задачу после выполнения
)

# Автоматическое обнаружение задач
celery.autodiscover_tasks(["tasks"])

