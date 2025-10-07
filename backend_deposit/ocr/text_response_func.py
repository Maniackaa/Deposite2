import datetime
import logging

import pytz
import structlog

from backend_deposit.settings import TIME_ZONE

logger = structlog.get_logger('deposit')
err_log = logging.getLogger(__name__)
tz = pytz.timezone(TIME_ZONE)


def get_unrecognized_field_error_text(response_fields, result):
    """Добавляет текст ошибки по полю если оно пустое (не распозналось)"""
    errors = []
    for field in response_fields:
        field_value = result.get(field)
        if field_value is None:
            error_text = f'Не распознано поле {field} при распознавании шаблона {result.get("type")}. result: {result}'
            errors.append(error_text)
            logger.warning(error_text)
    return errors


def date_response(data_text: str) -> datetime.datetime:
    """Преобразование строки в datetime"""
    # logger.debug(f'Распознавание даты из текста: {data_text}')
    try:
        native_datetime = datetime.datetime.fromisoformat(data_text.strip()) - datetime.timedelta(hours=1)
        response_data = tz.localize(native_datetime)
        return response_data
    except ValueError:
        pass
    formats = [
        '%d/%m/%y %H:%M:%S',
        '%d.%m.%y %H:%M',
        '%H:%M %d.%m.%y',
        '%d %B %Y %H:%M',
        '%d %B %Y%H:%M',
        '%d.%m.%Y %H:%M'
    ]
    for date_format in formats:
        try:
            native_datetime = datetime.datetime.strptime(data_text.strip(), date_format) - datetime.timedelta(hours=1)
            response_data = tz.localize(native_datetime)
            return response_data
        except ValueError as e:
            # print(e)
            pass
        except Exception as err:
            err_log.error(f'Ошибка распознавания даты из текста: {err}')
            raise err


# date_response('2023-08-19 12:30:14')
# print(date_response('03/10/23 19:55:27'))
# print(date_response('03.10.23 20:54'))
# print(date_response('20:08 30.06.24'))

def response_operations(fields: list[str], groups: tuple[str, ...], response_fields, sms_type: str) -> dict:
    result = dict.fromkeys(fields)
    result['type'] = sms_type
    errors = []
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
            logger.error(error_text, exc_info=True)
            errors.append(error_text)

    errors.extend(get_unrecognized_field_error_text(response_fields, result))
    result['errors'] = errors
    return result


def float_digital(string):
    result = ''.join([c if c in ['.', '-'] or c.isdigit() else '' for c in string])
    return float(result)


def response_sms1(fields: list[str], groups: tuple[str]) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 1 (Транзакция не прошла) Возвращаем сумму 0 и ошибку
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :param groups: ('Bloklanmish kart', '4127***6869', '2023-08-22 15:17:19', 'P2P SEND- LEO APP', '29.00', '569.51')
    :return: dict[str, str | float]
    """
    logger.debug('Распознавание шаблона sms1')
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        'recipient':        {'pos': 1},
        'sender':           {'pos': 3},
        'pay':              {'pos': 4, 'func': float_digital},
        'balance':          {'pos': 5, 'func': float_digital},
        'intima':           {'pos': 0}
    }
    sms_type = 'sms1'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        # Обнуление суммы и добавление ошибки
        errors = result.get('errors', [])
        errors.append(f'Платеж с суммой {result.get("pay")} не прошел. ({result.get("intima")})')
        result['errors'] = errors
        result['pay'] = 0
        result.pop('intima')
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


# x = response_sms1(['response_date', 'sender', 'bank', 'pay', 'balance', 'transaction', 'type'], ('Bloklanmish kart', '4127***6869', '2023-08-22 25:17:19', 'P2P SEND- LEO APP', '29.00', '569.51'))
# print(x)


def response_sms1b(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 1b (как 2)
    Mebleg:+-4.00 AZN (Original:4.00 AZN) - это поступление
    Mebleg:-2.00 AZN (Original: 1.00 AZN) - это списание
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :param groups: ('+80.00', '4127*6869', '2023-08-22 15:17:19', 'P2P SEND- LEO APP', '569.51')
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        'recipient':           {'pos': 1},
        'sender':             {'pos': 3},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms1b'
    try:
        # обработка дурацкого +-
        new_groups = []
        for val in groups:
            if '+-' in val:
                new_val = val.replace('+-', '+')
                new_groups.append(new_val)
            else:
                new_groups.append(val)
        new_groups_tuple: tuple[str, ...] = tuple(new_groups)
        result = response_operations(fields, new_groups_tuple, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms2(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 2
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :param groups: ('+80.00', '4127*6869', '2023-08-22 15:17:19', 'P2P SEND- LEO APP', '569.51')
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        'recipient':           {'pos': 1},
        'sender':             {'pos': 3},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms2'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err

# sms2 = response_sms2(['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type'], ('-1 080.00', '4127*6869', '2023-08-22 15:17:19', 'P2P SEND- LEO APP', '569.51'))
# print(sms2)


def response_sms3(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 3
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :param groups: ('5.00', '1000020358154 terminal payment ', '2023-08-19 02:30:14', '319.38')
                   ('10.01', 'ACCOUNT TO ACCOUNT TRANSFER   ', '2023-08-23 00:00:00', '222.96')
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        # 'sender':           {'pos': 1, 'func': lambda x: x.split('terminal payment')[0].strip() if 'terminal payment' in x else x},
        'sender':           {'pos': 1},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 3, 'func': float_digital},
    }
    sms_type = 'sms3'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms4(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 4
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :param groups: ('+80.00', '*5559', '2023-09-11 16:15', 'www.birbank.az', '861.00')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        'recipient':           {'pos': 1},
        'sender':             {'pos': 3},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms4'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms5(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 5
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('0.01', '***7680', 'P2P SEND- LEO APP, AZ', '03.10.23 20:54', '1.01 )
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date':    {'pos': 3, 'func': date_response},
        'recipient':        {'pos': 1},
        'sender':           {'pos': 2},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms5'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms6(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 6
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('0.01', '***7680', 'P2P SEND- LEO APP, AZ', '03.10.23 20:54', '1.01 )
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date':    {'pos': 3, 'func': date_response},
        'recipient':        {'pos': 1},
        'sender':           {'pos': 2},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms5'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms7(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 7
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('+1', 'www.birban', '2', ' *4197)
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'recipient':        {'pos': 3},
        'sender':           {'pos': 1},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 2, 'func': float_digital},
    }
    sms_type = 'sms7'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms8(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 8
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('1.00', 'M10 ACCOUNT TO CARD', '1.00')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'sender':           {'pos': 1},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 2, 'func': float_digital},
    }
    sms_type = 'sms8'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms9(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 9
    Balans artimi 4169**2259 Medaxil 3.00 AZN 16:25 13.03.24 BALANCE 2.07 AZN
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('Balans artimi', '4169**2259', '3.00', '16:25 13.03.24', '2.07')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 3, 'func': date_response},
        'sender':        {'pos': 0},
        'recipient':     {'pos': 1},
        'pay':           {'pos': 2, 'func': float_digital},
        'balance':       {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms9'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms10(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 10
    P2P SEND-LEO APP 4169**2259 Medaxil 1.00 AZN BALANCE 5.57 AZN 16:47 13.03.24
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('P2P SEND- LEO APP', '4169**2259', '1.00', '5.57', '16:47 13.03.24')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 4, 'func': date_response},
        'sender':        {'pos': 0},
        'recipient':     {'pos': 1},
        'pay':           {'pos': 2, 'func': float_digital},
        'balance':       {'pos': 3, 'func': float_digital},
    }
    sms_type = 'sms10'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms11(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 11
    Odenis 1.00 AZN M10 TOP UP BAKI AZERBAIJAN 4169**2259 16:28 13.03.24 BALANCE 1.07 AZN
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('1.00', 'M10 TOP UP BAKI AZERBAIJAN', '4169**2259', '16:47 13.03.24', '5.57')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 3, 'func': date_response},
        'sender':        {'pos': 1},
        'recipient':     {'pos': 2},
        'pay':           {'pos': 0, 'func': float_digital},
        'balance':       {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms11'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        result['pay'] = -result['pay']
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms12(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 12
    Card-to-Card: 19.03.24 19:34 M10 ACCOUNT TO CARD, AZ Card: ****7297 amount: 1.00 AZN Fee: 0.00 Balance: -4.00 AZN. Thank you. BankofBaku
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('19.03.24 19:34', 'M10 ACCOUNT TO CARD,', '****7297', '1.00', '-4.00')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 0, 'func': date_response},
        'sender':        {'pos': 1},
        'recipient':     {'pos': 2},
        'pay':           {'pos': 3, 'func': float_digital},
        'balance':       {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms12'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms13(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 13
    Odenis: 34.00 AZN BAKU CITY 5239**8563 19:17 28.06.24 BALANCE 0.96 AZN
    :param fields: ['recipient', 'sender', 'pay', 'balance', 'type']
    :param groups: ('1.00', 'M10 TOP UP BAKI AZERBAIJAN', '4169**2259', '16:47 13.03.24', '5.57')
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 3, 'func': date_response},
        'sender':        {'pos': 1},
        'recipient':     {'pos': 2},
        'pay':           {'pos': 0, 'func': float_digital},
        'balance':       {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms13'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        result['pay'] = result['pay']
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms14(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 14
    Medaxil: 1.10 AZN
    5239**1098
    20:08 30.06.24
    BALANCE
    9.30 AZN
    :param fields: ['recipient', 'pay', 'balance', 'type']
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 2, 'func': date_response},
        # 'sender':        {'pos': 1},
        'recipient':     {'pos': 1},
        'pay':           {'pos': 0, 'func': float_digital},
        'balance':       {'pos': 3, 'func': float_digital},
    }
    sms_type = 'sms14'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        result['pay'] = result['pay']
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms15(fields, groups) -> dict[str, str | float]:
    """
    Функия распознавания шаблона 15
    Medaxil C2C: 10.00 AZN
    BAKU
    5239**1098
    12:28 01.07.24
    BALANCE
    30.40 AZN
    :param fields: ['recipient', 'pay', 'balance', 'type']
    :return: dict[str, str | float]
    """
    logger.debug(f'fields:{fields} groups:{groups}')
    response_fields = {
        'response_date': {'pos': 3, 'func': date_response},
        'sender':        {'pos': 1},
        'recipient':     {'pos': 2},
        'pay':           {'pos': 0, 'func': float_digital},
        'balance':       {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms15'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        result['pay'] = result['pay']
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms16(fields, groups) -> dict[str, str | float]:
    """
    Uspeshnaya tranzakciya
    Summa:2.00 AZN
    Karta:5462*6164
    Data:11/08/24 17:28:03
    Merchant:www.birbank.az
    Balans:7.10 AZN
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        'recipient':           {'pos': 1},
        'sender':             {'pos': 3},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms16'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms17(fields, groups) -> dict[str, str | float]:
    """
    24.01.25 18:58
    M10 TOP UP,
    AZ KART: ****8512
    MEBLEG:-2.00 AZN
    KOM:0.00
    Balans:7.00AZN
    ID: 699288 TESEKKUR EDIRIK.BANK OF BAKU.INFO:145
    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 0, 'func': date_response},
        'recipient':           {'pos': 2},
        'sender':             {'pos': 1},
        'pay':              {'pos': 3, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms17'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms18(fields, groups) -> dict[str, str | float]:
    """
    KREDIT: 24.01.25 18:56 M10 ACCOUNT TO CARD, AZ Card: ****8512 MEBLEG:2.00 AZN KOM:0.00 Balans: 9.00 AZN. TESEKKUR EDIRIK. BANK OF BAKU

    :param fields: ['response_date', 'recipient', 'sender', 'pay', 'balance', 'transaction', 'type']
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 0, 'func': date_response},
        'recipient':           {'pos': 2},
        'sender':             {'pos': 1},
        'pay':              {'pos': 3, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms18'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err

def response_sms19(fields, groups) -> dict[str, str | float]:
    """
    Kredit:+1.00 AZN  5310***2174  Tarix:2025-09-09 20:34:33  Detal:C2C - DOMESTIC CARDS  Balans:1.00 AZN

    :param fields: ['pay',  'recipient', 'response_date', 'sender', 'balance']
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 2, 'func': date_response},
        'recipient':           {'pos': 1},
        'sender':             {'pos': 3},
        'pay':              {'pos': 0, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms19'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err


def response_sms20(fields, groups) -> dict[str, str | float]:
    """
    Depozit
    1.00 AZN
    4169**4484
    21:25 03.70.25
    BALANCE
    -1.00 AZN (C)KB

    :param fields: ['pay',  'recipient', 'response_date', 'sender', 'balance']
    :return: dict[str, str | float]
    """
    response_fields = {
        'response_date':    {'pos': 3, 'func': date_response},
        'recipient':           {'pos': 2},
        'sender':             {'pos': 0},
        'pay':              {'pos': 1, 'func': float_digital},
        'balance':          {'pos': 4, 'func': float_digital},
    }
    sms_type = 'sms19'
    try:
        result = response_operations(fields, groups, response_fields, sms_type)
        return result
    except Exception as err:
        err_log.error(f'Неизвестная ошибка при распознавании: {fields, groups} ({err})')
        raise err