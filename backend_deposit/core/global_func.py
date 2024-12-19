import hashlib
import logging

import pytz
import requests

from backend_deposit import settings
from backend_deposit.settings import TIME_ZONE

TZ = pytz.timezone(TIME_ZONE)
logger = logging.getLogger(__name__)


def send_message_tg(message: str, chat_ids: list = settings.ADMIN_IDS):
    """Отправка сообщений через чат-бот телеграмма"""
    try:
        for chat_id in chat_ids:
            logger.debug(f'Отправляем сообщение для {chat_id}')
            url = (f'https://api.telegram.org/'
                   f'bot{settings.BOT_TOKEN}/'
                   f'sendMessage?'
                   f'chat_id={chat_id}&'
                   f'text={message}&parse_mode=html')
            response = requests.get(url)
            if response.status_code == 200:
                logger.debug(f'Сообщение для {chat_id} отправлено')
            else:
                logger.error(f'Ошибка при отправке сообщения для {chat_id}. Код {response.status_code}')
    except Exception as err:
        logger.error(f'Ошибка при отправки сообщений: {err}')


def hash_gen(text, salt):
    """
    merchant_id + amount + salt
    :param text:
    :param salt:
    :return:
    """
    formatted_string = f'{text}' + f'{salt}'
    m = hashlib.sha256(formatted_string.encode('UTF-8'))
    return m.hexdigest()
