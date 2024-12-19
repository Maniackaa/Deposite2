import json

import requests
import structlog

from django.conf import settings

from backend_deposit.settings import BASE_DIR
from users.models import Options

log = structlog.get_logger(__name__)


data = {
    'refresh': '',
    'access': ''
}


token_file = BASE_DIR / 'token_asu.txt'


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


def get_asu_token() -> str:
    if not token_file.exists():
        get_new_asu_token()
    with open(token_file, 'r') as file:
        token_data = json.loads(file.read())
        token = token_data.get('access', '')
        print(f'read token: {token}')
        return token


def create_payment(payment_data):
    logger = log
    try:
        logger.debug(f'Создание заявки Payment на asu-pay: {payment_data}')
        # {'merchant': 34, 'order_id': 1586, 'amount': 1560.0, 'user_login': '119281059', 'pay_type': 'card_2'}
        token = get_asu_token()
        headers = {
            'Authorization': f'Bearer {token}'
        }
        url = f'{settings.ASU_HOST}/api/v1/payment/'
        response = requests.post(url, json=payment_data, headers=headers)
        if response.status_code == 401:
            headers = {
                'Authorization': f'Bearer {get_new_asu_token()}'
            }
            response = requests.post(url, json=payment_data, headers=headers)

        logger.debug(f'response: {response} {response.reason} {response.text}')
        if response.status_code == 201:
            return response.json()['id']
    except Exception as err:
        logger.debug(f'Ошибка при создании payment: {err}')


def send_card_data(payment_id, card_data) -> dict:
    logger = log.bind(payment_id=payment_id)
    try:
        logger.debug(f'Передача card_data {payment_id} на asu-pay')
        token = get_asu_token()
        headers = {
            'Authorization': f'Bearer {token}'
        }
        url = f'{settings.ASU_HOST}/api/v1/payment/{payment_id}/send_card_data/'
        response = requests.put(url, json=card_data, headers=headers)
        if response.status_code == 401:
            headers = {'Authorization': f'Bearer {get_new_asu_token()}'}
            response = requests.put(url, json=card_data, headers=headers)
        logger.debug(f'response {payment_id}: {response} {response.reason} {response.text}')
        if response.status_code == 200:
            return response.json()
    except Exception as err:
        logger.debug(f'Ошибка при передачи card_data {payment_id}: {err}')


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
