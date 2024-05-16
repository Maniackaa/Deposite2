import datetime
import json
import random

import structlog
from celery import shared_task
from requests import request

logger = structlog.get_logger(__name__)


@shared_task()
def send_merch_webhook(url, data: dict):
    try:
        logger.info(f'Отправка webhook на {url}: {json.dumps(data)}')
        headers = {"Content-Type": "application/json"}
        response = request(url=url, method='post', json=json.dumps(data), headers=headers, timeout=5)
        logger.info(response.status_code)
        return response.status_code
    except Exception as err:
        logger.error(err)