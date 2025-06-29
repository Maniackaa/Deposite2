import hashlib
import logging
import time

import pytz
import requests
import structlog

from backend_deposit import settings
from backend_deposit.settings import TIME_ZONE

TZ = pytz.timezone(TIME_ZONE)
logger = structlog.get_logger('deposit')


def send_message_tg(message: str, chat_ids: list = settings.ADMIN_IDS):
    if not message:
        return
    """Отправка сообщений через чат-бот телеграмма"""
    try:
        for chat_id in chat_ids:
            logger.debug(f'Отправляем сообщение для {chat_id}. Текст:\n{message}')
            url = (f'https://api.telegram.org/'
                   f'bot{settings.BOT_TOKEN}/'
                   f'sendMessage?'
                   f'chat_id={chat_id}&'
                   f'text={message}&parse_mode=html')
            response = requests.get(url)
            if response.status_code == 200:
                logger.debug(f'Сообщение для {chat_id} отправлено')
            else:
                logger.error(f'Ошибка при отправке сообщения для {chat_id}. Код {response.status_code} {response.text}')
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



def mask_compare(mask1, mask2):
    if not mask1 or not mask2:
        return False
    def get_visible_parts(card_mask):
        # Берём подряд цифры с начала
        start_digits = ''
        for c in card_mask:
            if c.isdigit():
                start_digits += c
            elif c in '*•.':
                break
        # Берём подряд цифры с конца
        end_digits = ''
        for c in reversed(card_mask):
            if c.isdigit():
                end_digits = c + end_digits
            elif c in '*•.':
                break
        return start_digits, end_digits
    mask1 = mask1.strip()
    mask2 = mask2.strip()
    start1, end1 = get_visible_parts(mask1)
    start2, end2 = get_visible_parts(mask2)
    # Сравниваем первые N символов, где N - минимальная длина начальных видимых цифр
    n_start = min(len(start1), len(start2))
    n_end = min(len(end1), len(end2))
    start_match = (start1[:n_start] == start2[:n_start]) if n_start else True
    end_match = (end1[-n_end:] == end2[-n_end:]) if n_end else True
    return start_match and end_match


class Timer:

    def __init__(self, text):
        self.text = text
        super().__init__()

    def __enter__(self):
        self.start = time.perf_counter()

    def __exit__(self, exc_type, exc_val, exc_tb):
        end = time.perf_counter()
        delta = end - self.start
        print(f'Время выполнения "{self.text}": {round(delta,2)} c.')
        logger.debug(f'Время выполнения "{self.text}": {round(delta,2)} c.')

if __name__ == '__main__':
    print(mask_compare('531599****9459', '5*459'))  # True
    print(mask_compare('531599****9459', '5315**9459'))  # True
    print(mask_compare('1234****5678', '1234****567'))  # False
    print(mask_compare('1234****5678', '1234****5678'))  # True
    print(mask_compare('****5678', '*5678'))  # True
    print(mask_compare('****5678', '1234****5678'))  # True
    print(mask_compare('1234****5678', '****5678'))  # True
    print(mask_compare('531599****9459', '*9459'))

    print(mask_compare('5315992157686244', '5315*244'))
    print(mask_compare('531599*****7741', '5315992157497741'))
