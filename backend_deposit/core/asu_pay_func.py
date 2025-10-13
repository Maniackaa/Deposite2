import json
import requests
import structlog

from django.conf import settings
from core.global_func import hash_gen, send_message_tg
from users.models import Options

log = structlog.get_logger('deposit')


data = {
    'refresh': '',
    'access': ''
}


token_file = settings.BASE_DIR / 'token_asu.txt'
token_birpay_file = settings.BASE_DIR / 'token_asu_birpay.txt'

def get_new_asu_token():
    logger = log
    logger.info(f'Получение первичного токена по логину')
    try:
        # login = settings.ASUPAY_LOGIN
        # password = settings.ASUPAY_PASSWORD
        options = Options.load()
        login = options.asu_login
        password = options.asu_password
        url = f"{settings.ASU_HOST}/api/v1/token/"
        payload = json.dumps({
            "username": login,
            "password": password
        })
        headers = {'Content-Type': 'application/json'}
        response = requests.request("POST", url, headers=headers, data=payload, timeout=5)
        logger.info(response.status_code)
        token_dict = response.json()
        data['refresh'] = token_dict.get('refresh')
        data['access'] = token_dict.get('access')
        token = token_dict.get('access')
        with open(token_file, 'w') as file:
            file.write(json.dumps(data))
        logger.info(f'data: {data}')
        return token
    except Exception as err:
        logger.error(f'Ошибка получения токена по логину/паролю: {err}')
        raise err

# def get_new_asu_birpay_token():
#     logger = log
#     logger.info(f'Получение первичного токена по логину для BirPayShop')
#     try:
#         options = Options.load()
#         login = options.asu_birshop_login
#         password = options.asu_birshop_password
#         url = f"{settings.ASU_HOST}/api/v1/token/"
#         payload = json.dumps({
#             "username": login,
#             "password": password
#         })
#         headers = {'Content-Type': 'application/json'}
#         response = requests.request("POST", url, headers=headers, data=payload, timeout=5)
#         logger.info(response.status_code)
#         token_dict = response.json()
#         token = token_dict.get('access')
#         with open(token_birpay_file, 'w') as file:
#             file.write(json.dumps(data))
#         logger.info(f'data: {data}')
#         return token
#     except Exception as err:
#         logger.error(f'Ошибка получения токена по логину/паролю: {err}')
#         raise err


def get_asu_token() -> str:
    if not token_file.exists():
        get_new_asu_token()
    with open(token_file, 'r') as file:
        token_data = json.loads(file.read())
        token = token_data.get('access', '')
        print(f'read token: {token}')
        return token


# def get_asu_birpay_token() -> str:
#     # Для BirPayShop
#     if not token_birpay_file.exists():
#         get_new_asu_birpay_token()
#     with open(token_birpay_file, 'r') as file:
#         token_data = json.loads(file.read())
#         token = token_data.get('access', '')
#         print(f'read token: {token}')
#         return token


# def create_payment(payment_data):
#     logger = log
#     try:
#         logger.debug(f'Создание заявки Payment на asu-pay: {payment_data}')
#         # {'merchant': 34, 'order_id': 1586, 'amount': 1560.0, 'user_login': '119281059', 'pay_type': 'card_2'}
#         token = get_asu_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         url = f'{settings.ASU_HOST}/api/v1/payment/'
#         response = requests.post(url, json=payment_data, headers=headers)
#         if response.status_code == 401:
#             headers = {
#                 'Authorization': f'Bearer {get_new_asu_token()}'
#             }
#             response = requests.post(url, json=payment_data, headers=headers)
#
#         logger.debug(f'response: {response} {response.reason} {response.text}')
#         if response.status_code == 201:
#             return response.json()['id']
#         else:
#             text = f'Ошибка создания платежа на АСУ. response: {response} {response.reason} {response.text}'
#             logger.error(text)
#             send_message_tg(message=text, chat_ids=settings.ALARM_IDS)
#     except Exception as err:
#         logger.debug(f'Ошибка при создании payment: {err}')
#
#
# def send_card_data(payment_id, card_data) -> dict:
#     logger = log.bind(payment_id=payment_id)
#     try:
#         logger.debug(f'Передача card_data {payment_id} на asu-pay')
#         token = get_asu_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         url = f'{settings.ASU_HOST}/api/v1/payment/{payment_id}/send_card_data/'
#         response = requests.put(url, json=card_data, headers=headers)
#         if response.status_code == 401:
#             headers = {'Authorization': f'Bearer {get_new_asu_token()}'}
#             response = requests.put(url, json=card_data, headers=headers)
#         logger.debug(f'response {payment_id}: {response} {response.reason} {response.text}')
#         if response.status_code == 200:
#             return response.json()
#     except Exception as err:
#         logger.debug(f'Ошибка при передачи card_data {payment_id}: {err}')


def send_sms_code(payment_id, sms_code, transaction_id=None) -> dict:
    if transaction_id:
        logger = log.bind(transaction_id=transaction_id, payment_id=payment_id)
    else:
        logger = log.bind(payment_id=payment_id)
    try:

        logger.debug(f'Передача sms_code {payment_id} на asu-pay')
        token = get_asu_token()
        headers = {
            'Authorization': f'Bearer {token}'
        }
        url = f'{settings.ASU_HOST}/api/v1/payment/{payment_id}/send_sms_code/'
        json_data = {'sms_code': sms_code}
        response = requests.put(url, json=json_data, headers=headers)
        if response.status_code == 401:
            headers = {'Authorization': f'Bearer {get_new_asu_token()}'}
            response = requests.put(url, json=json_data, headers=headers)
        logger.debug(f'response {payment_id}: {response} {response.reason} {response.text}')
        if response.status_code == 200:
            return response.json()
    except Exception as err:
        logger.debug(f'Ошибка при передачи card_data {payment_id}: {err}')


def create_payment_v2(payment_data, card_data=None):
    """
    Создание платежа через API v2 с данными карты
    Объединяет функциональность create_payment и send_card_data из v1
    """
    logger = log
    try:
        logger.debug(f'Создание заявки Payment на asu-pay v2: {payment_data}')

        # Подготавливаем данные для API v2
        v2_payment_data = {
            'merchant': payment_data['merchant'],
            'order_id': payment_data['order_id'],
            'amount': payment_data['amount'],
            'pay_type': payment_data['pay_type'],
            'currency_code': payment_data.get('currency_code', 'AZN'),
            'user_login': payment_data.get('user_login', ''),
        }

        # Добавляем данные карты если они есть
        if card_data:
            v2_payment_data['card_data'] = card_data

        token = get_asu_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        url = f'{settings.ASU_HOST}/api/v2/payment/'

        response = requests.post(url, json=v2_payment_data, headers=headers)

        if response.status_code == 401:
            headers = {
                'Authorization': f'Bearer {get_new_asu_token()}',
                'Content-Type': 'application/json'
            }
            response = requests.post(url, json=v2_payment_data, headers=headers)

        logger.debug(f'response: {response} {response.reason} {response.text}')

        if response.status_code == 201:
            response_data = response.json()
            return {
                'payment_id': response_data['id'],
                'sms_required': response_data.get('sms_required', False),
                'instruction': response_data.get('instruction', '')
            }
        else:
            text = f'Ошибка создания платежа на АСУ v2. response: {response} {response.reason} {response.text}'
            logger.error(text)
            send_message_tg(message=text, chat_ids=settings.ALARM_IDS)
            return None

    except Exception as err:
        logger.debug(f'Ошибка при создании payment v2: {err}')
        return None


def send_sms_code_v2(payment_id, sms_code, transaction_id=None):
    """
    Отправка SMS-кода через API v2
    """
    if transaction_id:
        logger = log.bind(transaction_id=transaction_id, payment_id=payment_id)
    else:
        logger = log.bind(payment_id=payment_id)

    try:
        logger.debug(f'Передача sms_code {payment_id} на asu-pay v2')

        token = get_asu_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        url = f'{settings.ASU_HOST}/api/v2/payment/{payment_id}/send_sms_code/'

        json_data = {'sms_code': sms_code}
        response = requests.put(url, json=json_data, headers=headers)

        if response.status_code == 401:
            headers = {
                'Authorization': f'Bearer {get_new_asu_token()}',
                'Content-Type': 'application/json'
            }
            response = requests.put(url, json=json_data, headers=headers)

        logger.debug(f'response {payment_id}: {response} {response.reason} {response.text}')

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f'Ошибка отправки SMS-кода: {response.status_code} {response.text}')
            return None

    except Exception as err:
        logger.debug(f'Ошибка при передачи sms_code {payment_id}: {err}')
        return None


# def create_birpay_payment(payment_data):
#     logger = log
#     try:
#         logger.debug(f'Создание заявки Payment на asu-pay: {payment_data}')
#         # {'merchant': 34, 'order_id': 1586, 'amount': 1560.0, 'user_login': '119281059', 'pay_type': 'card_2'}
#         token = get_asu_birpay_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         url = f'{settings.ASU_HOST}/api/v1/payment/'
#         response = requests.post(url, json=payment_data, headers=headers)
#         if response.status_code == 401:
#             headers = {
#                 'Authorization': f'Bearer {get_new_asu_birpay_token()}'
#             }
#             response = requests.post(url, json=payment_data, headers=headers)
#
#         logger.debug(f'response: {response} {response.reason} {response.text}')
#         if response.status_code == 201:
#             return response.json()['id']
#     except Exception as err:
#         logger.debug(f'Ошибка при создании payment: {err}')


# def send_card_data_birshop(payment_id, card_data) -> dict:
#     logger = log.bind(payment_id=payment_id)
#     try:
#         logger.debug(f'Передача card_data {payment_id} на asu-pay от BirShop')
#         token = get_asu_birpay_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         url = f'{settings.ASU_HOST}/api/v1/payment/{payment_id}/send_card_data/'
#         response = requests.put(url, json=card_data, headers=headers)
#         if response.status_code == 401:
#             headers = {'Authorization': f'Bearer {get_new_asu_birpay_token()}'}
#             response = requests.put(url, json=card_data, headers=headers)
#         logger.debug(f'response {payment_id}: {response} {response.reason} {response.text}')
#         if response.status_code == 200:
#             return response.json()
#     except Exception as err:
#         logger.debug(f'Ошибка при передачи card_data {payment_id}: {err}')


# def send_sms_code_birpay(payment_id, sms_code, transaction_id=None) -> dict:
#     if transaction_id:
#         logger = log.bind(transaction_id=transaction_id, payment_id=payment_id)
#     else:
#         logger = log.bind(payment_id=payment_id)
#     try:
#
#         logger.debug(f'Передача sms_code {payment_id} на asu-pay')
#         token = get_asu_birpay_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         url = f'{settings.ASU_HOST}/api/v1/payment/{payment_id}/send_sms_code/'
#         json_data = {'sms_code': sms_code}
#         response = requests.put(url, json=json_data, headers=headers)
#         if response.status_code == 401:
#             headers = {'Authorization': f'Bearer {get_new_asu_birpay_token()}'}
#             response = requests.put(url, json=json_data, headers=headers)
#         logger.debug(f'response {payment_id}: {response} {response.reason} {response.text}')
#         if response.status_code == 200:
#             return response.json()
#     except Exception as err:
#         logger.debug(f'Ошибка при передачи card_data {payment_id}: {err}')


def create_asu_withdraw(withdraw_id, amount, card_data, target_phone, payload: dict):
    """{'amount': '198.0000',
    'createdAt': '2025-02-15T15:56:11+03:00',
    'currency': 'AZN',
    'customerName': 'KAJEN PRASASTA',
    'customerWalletId': '4169738808592590',
    'id': 12099262,
    'merchant': {'id': 2,
               'name': '8billing',
               'uid': '348d92c9-cdbd-48b8-ae62-4d66c200b878'},
              'merchantTransactionId': '43903147',
              'operatorTransactionId': '',
              'payload': {'card_date': '03/2026'},
              'payoutMethodType': {'id': 22, 'name': 'AZN_azcashier'},
              'status': 0,
              'uid': 'b3429f24-432b-4796-a8e2-986c39fbbdf7',
              'updatedAt': '2025-02-15T15:56:11+03:00'}"""
    result = {}
    log = structlog.get_logger('deposit')
    logger = log.bind(birpay_withdraw_id=withdraw_id)
    try:

        options = Options.load()
        merchant_id = options.asu_merchant_id
        text = f'{merchant_id}{target_phone or card_data.get("card_number")}{int(round(amount, 0))}'
        secret = options.asu_secret
        signature = hash_gen(text, secret)
        withdraw_data = {
            "merchant": f'{merchant_id}',
            "withdraw_id": withdraw_id,
            # "payload": {"merchant_transaction_id": merchant_transaction_id},
            "payload": payload,
            "amount": f'{amount}',
            "currency_code": "AZN",
            "signature": signature,
        }
        if card_data:
            withdraw_data['card_data'] = card_data
        if target_phone:
            withdraw_data['target_phone'] = target_phone
        asu_token = get_asu_token()
        headers = {
            'Authorization': f'Bearer {asu_token}'
        }
        url = f'{settings.ASU_HOST}/api/v1/withdraw/'
        logger.info(f'Отправка на асупэй birpay_withdraw_data: {withdraw_data}')
        response = requests.post(url, json=withdraw_data, headers=headers)
        logger.debug(f'response: {response} {response.reason} {response.text}')
        if response.status_code == 401:
            headers = {
                'Authorization': f'Bearer {get_new_asu_token()}'
            }
            response = requests.post(url, json=withdraw_data, headers=headers)
        if response.status_code == 201:

            result = response.json()
            logger.debug(f'Успешно создан на Asupay')
        else:
            logger.warning(f'response: {response} {response.reason} {response.text}')
        logger.info(f'Результат: {result}')
        return result
    except Exception as err:
        result = {'withdraw_id': withdraw_id, 'status': 'error', 'error': err}
        logger.debug(f'Ошибка при создании withdraw: {err}')
        return result

# def check_asu_payment_for_card(card_number: str, status=(0, 1, 2, 3, 4, 5, 6, 7, 8), amount=''):
#     """
#     Возвращает список платежей с указанной картой, статусом и суммой
#
#     Parameters
#     ----------
#     card_number
#     status
#     amount
#
#     Returns
#     -------
#
#     """
#
#     logger = log
#     try:
#         logger.debug(f'Проверка активных платежей по карте')
#         token = get_asu_birpay_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         status_query = ','.join([str(x) for x in status])
#
#         url = f'{settings.ASU_HOST}/api/v1/payments_archive/?pay_type=card_2&card_number={card_number}&status={status_query}&amount={amount}'
#         logger.debug(f'url: {url}')
#         response = requests.get(url, headers=headers)
#         if response.status_code == 401:
#             headers = {
#                 'Authorization': f'Bearer {get_new_asu_birpay_token()}'
#             }
#             response = requests.get(url, headers=headers)
#         logger.debug(f'активных платежей по карте response: {response} {response.reason} {response.text}')
#         return response
#     except Exception as err:
#         logger.debug(f'Ошибка при создании payment: {err}')

#
# def gpt_response_check(data):
#     logger = log
#     try:
#         logger.debug(f'Запрос GPT на asu-pay: {data}')
#         token = get_asu_token()
#         headers = {
#             'Authorization': f'Bearer {token}'
#         }
#         url = f'{settings.ASU_HOST}/api/v2/gpt/check/'
#         response = requests.post(url, json=data, headers=headers)
#         if response.status_code == 401:
#             headers = {
#                 'Authorization': f'Bearer {get_new_asu_token()}'
#             }
#             response = requests.post(url, json=data, headers=headers)
#
#         logger.debug(f'response: {response} {response.reason} {response.text}')
#         if response.status_code == 201:
#             return response.json()
#     except Exception as err:
#         logger.debug(f'Ошибка при Запрос GPT на asu-pay: {err}')


if __name__ == '__main__':
    pass
    # x = check_asu_payment_for_card('4169738826837498')
    # print(x)