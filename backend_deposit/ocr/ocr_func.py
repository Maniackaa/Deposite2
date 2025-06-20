import datetime
import logging
from pathlib import Path
from sys import platform

import cv2
import numpy as np
import pytesseract
import pytz
import structlog

from backend_deposit.settings import TIME_ZONE


from ocr.text_response_func import date_response

TZ = pytz.timezone(TIME_ZONE)
logger = structlog.get_logger('deposit')


def get_unrecognized_field_error_text(response_fields, result):
    """Добавляет текст ошибки по полю если оно пустое (не распозналось)"""
    errors = []
    for field in response_fields:
        if not result.get(field):
            error_text = f'Не распознано поле {field} при распознавании шаблона {result.get("type")}'
            errors.append(error_text)
    return errors


def response_operations(fields: list[str], groups: tuple[str], response_fields, sms_type: str):
    result = dict.fromkeys(fields)
    result['type'] = sms_type
    errors = []
    print(fields, groups, response_fields)
    for key, options in response_fields.items():
        try:
            value = groups[options['pos']].strip().replace(',', '')
            if options.get('func'):
                func = options.get('func')
                result[key] = func(value)
            else:
                result[key] = value
        except Exception as err:
            error_text = f'Ошибка распознавания поля {key}: {err}'
            logger.error(error_text)
            errors.append(error_text)

    errors.extend(get_unrecognized_field_error_text(response_fields, result))
    result['errors'] = errors
    print(result)
    return result


def bytes_to_str(file_bytes, black=160, white=255, lang='rus'):
    try:
        # pytesseract.pytesseract.tesseract_cmd = r'/usr/local/bin/pytesseract'
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        img = img[100:, :]
        _, binary = cv2.threshold(img, black, white, cv2.THRESH_BINARY)
        string = pytesseract.image_to_string(binary, lang=lang, config='--oem 1', timeout=10)
        # string = pytesseract.image_to_string(binary, lang=lang)
        string = string.replace('\n', ' ')
        return string
    except Exception as err:
        logger.error(f'Ошибка в cv2 {err}', exc_info=True)


def date_m10_response(data_text: str) -> datetime.datetime:
    """Преобразование строки m10 в datetime"""
    logger.debug(f'Распознавание даты из текста: {data_text}')
    try:
        native_datetime = datetime.datetime.strptime(data_text, '%d.%m.%Y %H:%M')
        response_data = TZ.localize(native_datetime)
        return response_data
    except Exception as err:
        logger.error(f'Ошибка распознавания даты из текста: {err}')
        raise err


def response_m10(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона m10
    :param fields: ['response_date', 'sender', 'recipient', 'bank', 'pay', 'balance', 'transaction', 'type', 'status']
    :param groups: ('25.08.2023 01:07', '+994 51 927 05 68', '+994 70 *** ** 27', '55555150', '5.00 м', 'Успешно ')
    :return: dict[str, str | float]
    """
    # logger.debug('Преобразование текста m10 в pay')
    response_fields = {
        'response_date':    {'pos': 0, 'func': date_m10_response},
        'recipient':        {'pos': 1},
        'sender':           {'pos': 2},
        'pay':              {'pos': 4, 'func': lambda x: float(''.join([c if c in ['.', '-'] or c.isdigit() else '' for c in x]))},
        'transaction':      {'pos': 3, 'func': int},
        'status':           {'pos': 5},
    }
    sms_type = 'm10'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        logger.debug(result)
        return result
    except Exception as err:
        logger.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_m10new(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона m10new
    [('Bank of Baku', '-2157.00 m', 'Success', '02 July 2024,13:23', '+994 51 346 79 61', '5315 99 ------ 3934', '340552097')]
    """
    # logger.debug('Преобразование текста m10 в pay')
    response_fields = {
        'first':            {'pos': 0},
        'response_date':    {'pos': 3, 'func': date_response},
        'recipient':        {'pos': 5},
        'sender':           {'pos': 4},
        'pay':              {'pos': 1, 'func': lambda x: float(''.join([c if c in ['.', '-'] or c.isdigit() else '' for c in x]))},
        'transaction':      {'pos': 6, 'func': int},
        'status':           {'pos': 2},
    }
    sms_type = 'm10new'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        logger.debug(result)
        pay = result.get('pay')
        if pay and pay >= 0:
            result['sender'] = result['first']
        return result
    except Exception as err:
        logger.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_m10new_short(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона m10new short
    first: MiIIiON
    amount: +45.00 m
    Top—up
    Status Success
    Date 04 July 2024, 14:11
    m10 wallet +994 51 346 79 61
    Transaction ID 342689004
    """
    response_fields = {
        'first':            {'pos': 0},
        'response_date':    {'pos': 3, 'func': date_response},
        'recipient':        {'pos': 4},
        # 'sender':           {'pos': 4},
        'pay':              {'pos': 1, 'func': lambda x: float(''.join([c if c in ['.', '-'] or c.isdigit() else '' for c in x]))},
        'transaction':      {'pos': 5, 'func': int},
        'status':           {'pos': 2},
    }
    sms_type = 'm10new_short'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        logger.debug(result)
        pay = result.get('pay')
        if pay and pay >= 0:
            result['sender'] = result['first']
        return result
    except Exception as err:
        logger.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_m10_short(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона m10_short
    :param fields: ['response_date', 'recipient', 'bank', 'pay', 'balance', 'transaction', 'type', 'status']
    :param groups: ('27.08.2023 01:48', 'Пополнение С МИНОМ', '+994 51 927 05 68', '56191119', '10.00 м', 'Успешно')
    :return: dict[str, str | float]
    """
    logger.debug(f'Преобразование текста m10_short {groups}')
    response_fields = {
        'response_date':    {'pos': 0, 'func': date_m10_response},
        'recipient':        {'pos': 2},
        'sender':           {'pos': 1},
        'pay':              {'pos': 4, 'func': lambda x: float(''.join([c if c in ['.', '-'] or c.isdigit() else '' for c in x]))},
        'transaction':      {'pos': 3, 'func': int},
        'status':           {'pos': 5},
    }
    sms_type = 'm10_short'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        logger.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def make_after_incoming_save(instance):
    """
    Действия после сохранения скрина.
    Находит депозит не ранее 10 минут с такой-же суммой и транзакцией [-1, -1, +1]
    """
    from deposit.models import Deposit, Incoming
    try:
        if instance.confirmed_deposit:
            logger.debug('incoming post_save return')
            return
        logger.debug(f'Действие после сохранения корректного скрина: {instance}')
        pay = instance.pay
        transaction = instance.transaction
        transaction_list = [transaction - 1, transaction + 1, transaction - 2]
        threshold = datetime.datetime.now(tz=TZ) - datetime.timedelta(minutes=10)
        logger.debug(f'Ищем депозиты не позднее чем: {str(threshold)}')
        deposits = Deposit.objects.filter(
            status='pending',
            pay_sum=pay,
            register_time__gte=threshold,
            input_transaction__in=transaction_list
        ).all()
        logger.debug(f'Найденные deposits: {deposits}')
        if deposits:
            deposit = deposits.first()
            logger.debug(f'Подтверждаем депозит {deposit}')
            deposit.confirmed_incoming = instance
            deposit.status = 'confirmed'
            deposit.save()
            logger.debug(f'Депозит подтвержден: {deposit}')
            logger.debug(f'Сохраняем confirmed_deposit: {deposit}')
            instance.confirmed_deposit = deposit
            instance.save()

    except Exception as err:
        logger.error(err, exc_info=True)


def make_after_save_deposit(instance):
    """
    Действия после сохранения скрина.
    Находит депозит не ранее 10 минут с такой-же суммой и транзакцией [-2, -1, +1]
    """
    try:
        from deposit.models import Deposit, Incoming
        logger.debug(f'Действие после сохранения депозита: {instance}')
        if instance.input_transaction and instance.status == 'pending':
            threshold = datetime.datetime.now(tz=TZ) - datetime.timedelta(minutes=10)
            logger.debug(f'Ищем скрины не ранее чем: {str(threshold)}')
            logger.debug(f'input_transaction: {instance.input_transaction}, {type(instance.input_transaction)}')
            transaction_list = [instance.input_transaction - 1,
                                instance.input_transaction + 1,
                                instance.input_transaction + 2]
            logger.debug(f'transaction_list: {transaction_list}')
            incomings = Incoming.objects.filter(
                register_date__gte=threshold,
                pay=instance.pay_sum,
                transaction__in=transaction_list,
                confirmed_deposit=None
            ).order_by('-id').all()
            logger.debug(f'Найденные скрины: {incomings}')
            if incomings:
                incoming = incomings.first()
                incoming.confirmed_deposit = instance
                instance.status = 'approved'
                incoming.save()
                instance.save()
        else:
            logger.debug('deposit post_save return')

    except Exception as err:
        logger.error(err, exc_info=True)


def response_text_from_image(source: Path | bytes, y_start=None, y_end=None, x_start=None, x_end=None, strip=False, black=90, white=250, lang='eng',
                             oem=0, psm=6, char_whitelist=None) -> str:
    """
    Функция распознает переданный файл (байты) или путь.
    Parameters
    ----------
    source: картинка (байты или путь)
    y_start: Начало по вертикали в %
    y_end: Конец по вертикали в %
    strip: обрезать ли 20% по краям
    black
    white
    lang
    oem: движок
    psm

    Returns
    -------

    """
    if platform == 'win32':
        tespatch = Path('C:/') / 'Program Files' / 'Tesseract-OCR' / 'tesseract.exe'
        pytesseract.pytesseract.tesseract_cmd = tespatch.as_posix()
    if isinstance(source, Path):
        img = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    else:
        img = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    height, width = img.shape
    if y_start and y_end:
        if strip:
            img = img[int(y_start / 100 * height):int(y_end / 100 * height), int(20 / 100 * width):int(80 / 100 * width)]
        else:
            img = img[int(y_start / 100 * height):int(y_end / 100 * height), :]
    if x_start and x_end:
        img = img[:, int(x_start / 100 * width):int(x_end / 100 * width)]
    _, binary = cv2.threshold(img, black, white, cv2.THRESH_BINARY)
    # cv2.imwrite('preview.jpg', binary)
    # cv2.imshow('imname', img)
    # cv2.waitKey(0)
    config = f'--psm {psm} --oem {oem}'
    if char_whitelist:
        config += f'-c tessedit_char_whitelist="{char_whitelist}"'
    response_text = (pytesseract.image_to_string(binary, lang=lang, config=config, timeout=10)).strip()
    logger.info(response_text)
    return response_text
