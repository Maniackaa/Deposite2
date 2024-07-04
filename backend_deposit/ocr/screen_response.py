import logging
import re


from ocr.ocr_func import response_m10, response_m10_short, response_m10new, response_m10new_short

logger = logging.getLogger(__name__)


def screen_text_to_pay(text):
    """
    Шаблоны распознавания текста соскринов m10
    """
    logger.debug(f'Распознаем текст {text}')
    patterns = {
        'm10': r'.*(\d\d\.\d\d\.\d\d\d\d \d\d:\d\d).*Получатель (.*) Отправитель (.*) Код транзакции (\d+) Сумма (.+) Статус (.*) .*8',
        'm10_short': r'.*(\d\d\.\d\d\.\d\d\d\d \d\d:\d\d).* (Пополнение.*) Получатель (.*) Код транзакции (\d+) Сумма (.+) Статус (\S+).*',
        'm10new': r'first: (.+)[\n]+amount:.*[\n]*([+-].*)m.*[\n]+.*[\n]*.*[\n]*.*[\n]*.*[\n]*Status (.+)[\n]*Date (.+)[\n]+Sender (.+)[\n]*Recipient (.+)[\n]+.*ID (.+)',
        'm10new_short': r'first: (.+)[\n]+amount:.*([+-].*)m.*[\n]+.*[\n]*.*[\n]*.*[\n]*.*[\n]*Status (.+)[\n]+Date (.+)[\n]+m10 wallet (.+)[\n]+.*ID (.+)'
    }
    response_func = {
        'm10': response_m10,
        'm10_short': response_m10_short,
        'm10new': response_m10new,
        'm10new_short': response_m10new_short,
    }
    fields = ['response_date', 'recipient', 'sender', 'pay', 'balance',
              'transaction', 'type', 'status']
    text_sms_type = ''
    responsed_pay = {'status': '', 'type': '', 'errors': ''}
    errors = []
    status = ''
    for sms_type, pattern in patterns.items():
        logger.debug(f'Проверяем паттерн {sms_type}')
        search_result = re.findall(pattern, text, flags=re.I)
        logger.debug(f'{search_result}: {bool(search_result)}')
        if search_result:
            logger.debug(f'Найдено: {sms_type}: {search_result}')
            text_sms_type = sms_type  # m10 / m10_short
            responsed_pay: dict = response_func[text_sms_type](fields, search_result[0])
            # errors = responsed_pay.pop('errors')
            # status = responsed_pay.pop('status')
            break
    return responsed_pay
