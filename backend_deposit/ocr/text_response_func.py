import datetime
import logging

import pytz

from backend_deposit.settings import TIME_ZONE

logger = logging.getLogger(__name__)
err_log = logging.getLogger(__name__)
tz = pytz.timezone(TIME_ZONE)


def get_unrecognized_field_error_text(response_fields, result):
    """Добавляет текст ошибки по полю если оно пустое (не распозналось)"""
    errors = []
    for field in response_fields:
        if not result.get(field):
            error_text = f'Не распознано поле {field} при распознавании шаблона {result.get("type")}'
            errors.append(error_text)
    return errors


def date_response(data_text: str) -> datetime.datetime:
    """Преобразование строки в datetime"""
    logger.debug(f'Распознавание даты из текста: {data_text}')
    try:
        native_datetime = datetime.datetime.fromisoformat(data_text.strip()) - datetime.timedelta(hours=1)
        response_data = tz.localize(native_datetime)
        return response_data
    except ValueError:
        pass
    try:
        native_datetime = datetime.datetime.strptime(data_text.strip(), '%d/%m/%y %H:%M:%S') - datetime.timedelta(hours=1)
        response_data = tz.localize(native_datetime)
        return response_data
    except ValueError:
        pass
    try:
        native_datetime = datetime.datetime.strptime(data_text.strip(), '%d.%m.%y %H:%M') - datetime.timedelta(hours=1)
        response_data = tz.localize(native_datetime)
        return response_data
    except ValueError:
        pass
    try:
        native_datetime = datetime.datetime.strptime(data_text.strip(), '%H:%M %d.%m.%y') - datetime.timedelta(hours=1)
        response_data = tz.localize(native_datetime)
        return response_data
    except ValueError:
        pass
    except Exception as err:
        err_log.error(f'Ошибка распознавания даты из текста: {err}')
        raise err


# date_response('2023-08-19 12:30:14')
# print(date_response('03/10/23 19:55:27'))
# print(date_response('03.10.23 20:54'))


def response_operations(fields: list[str], groups: tuple[str], response_fields, sms_type: str) -> dict:
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