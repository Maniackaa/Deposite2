import datetime
import os

import requests
import structlog

from backend_deposit.settings import BASE_DIR
from users.models import Options
from core.global_func import TZ

logger = structlog.get_logger('tasks')


token_file = BASE_DIR / 'token_new.txt'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    'Content-Type': 'application/json;charset=utf-8',
    'Referer': 'https://old.um.money/refill-orders/list',
    'Origin': 'https://old.um.money',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Connection': 'keep-alive',
}


def get_new_token():
    logger.debug('Получение токена')
    options = Options.load()
    BIRPAY_NEW_LOGIN = options.um_login
    BIRPAY_NEW_PASSWORD = options.um_password
    # BIRPAY_NEW_LOGIN = os.getenv('BIRPAY_NEW_LOGIN')
    # BIRPAY_NEW_PASSWORD = os.getenv('BIRPAY_NEW_PASSWORD')
    json_data = {
        'email': BIRPAY_NEW_LOGIN,
        'password': BIRPAY_NEW_PASSWORD,
    }

    response = requests.post('https://api.um.money/api/dashboard/auth/login', headers=headers, json=json_data)
    logger.debug(response.status_code)
    if response.status_code == 200:
        token = response.json().get('token')
        with open(BASE_DIR / 'token_new.txt', 'w') as file:
            file.write(token)
        return token


def get_token():
    if not token_file.exists():
        get_new_token()
    with open(BASE_DIR / 'token_new.txt', 'r') as file:
        token = file.read()
        return token


def get_um_transactions(search_filter=None):
    """
    Получение списка транзакций на um.money
    Parameters
    ----------
    search_filter: {'id': '1596',
                   'merchantTransactionId': 'c37999db-6065-42b0-8b7a-ae6e8215e186',
                   'amount': {'from': 10, 'to': 20},
                   'status': ['approved', 'pending', 'declined', 'cancelled', 'new'],
                   'createdAt': '2024-10-08'
    Returns
    -------
    """
    if search_filter is None:
        search_filter = {}
    try:
        logger.debug('Запрос транзакций на um.money')
        token = get_token()
        headers['Authorization'] = f'Bearer {token}'
        json_data = {
            'filter': search_filter,
            'limit': {
                'page': 1,
                'limit': 10,
            },
        }

        response = requests.post('https://api.um.money/api/dashboard/refill-order/find',
                                 headers=headers, json=json_data)
        print(response)
        if response.status_code == 401:
            # Обновление токена
            token_new = get_new_token()
            headers['Authorization'] = f'Bearer {token_new}'
            response = requests.post('https://api.um.money/api/dashboard/refill-order/find',
                                     headers=headers, json=json_data)

        if response.status_code == 200:
            json_data = response.json()
            print(json_data)
            return json_data.get('data', [])

    except Exception as err:
        logger.error(err)
        raise err


def wait_sms(order_pk) -> int:
    json_data = {
        'action': 'agent_sms',
    }
    token = get_token()
    headers['Authorization'] = f'Bearer {token}'
    response = requests.put(
        f'https://api.um.money/api/dashboard/refill-order/{order_pk}/action',
        headers=headers,
        json=json_data,
    )
    return response.status_code


def create_payment_data_from_new_transaction(transaction_data: {}) -> dict:
    # Из новой транзакции формирует данные для создания заявки на asupay.
    # payload ['userId:93849951', 'cvv2:******', 'card_holder:HAMID HAMIDOV', 'card_number:******', 'expiry_date:01/26']
    options = Options.load()
    merchant_id = options.asu_merchant_id
    amount = float(transaction_data['amount'])
    payload = transaction_data['payload']
    create_at = transaction_data['createdAt']
    # create_at = datetime.datetime.fromisoformat(create_at)
    card_holder = user_login = card_number = expired_month = expired_year = cvv = ''
    for row in payload:
        field, value = row.split(':', 1)
        if field == 'userId':
            user_login = value
        elif field == 'card_holder':
            card_holder = value
        elif field == 'card_number':
            card_number = value
        elif field == 'expiry_date':
            expiry_date = value
            if '/' in expiry_date:
                expired_month, expired_year = expiry_date.split('/')
        elif field == 'cvv2':
            cvv = value

    order_id = transaction_data['id']
    payment_data = {
          "merchant": merchant_id,
          "order_id": order_id,
          "amount": amount,
          "user_login": user_login,
          "pay_type": "card_2",
        }

    card_data = {
        "card_number": card_number,
        "owner_name": card_holder,
        "expired_month": expired_month,
        "expired_year": expired_year,
        "cvv": cvv
    }
    return {'payment_data': payment_data, 'card_data': card_data}


def send_transaction_action(order_pk, action: str) -> dict:
    # agent_sms, agent_decline, agent_push
    try:
        logger.debug('Отправка action')
        json_data = {'action': action}
        response = requests.put(f'https://api.um.money/api/dashboard/refill-order/{order_pk}/action',
                                headers=headers, json=json_data)

        if response.status_code == 401:
            # Обновление токена
            token_new = get_new_token()
            headers['Authorization'] = f'Bearer {token_new}'

        response = requests.put(f'https://api.um.money/api/dashboard/refill-order/{order_pk}/action',
                                    headers=headers, json=json_data)

        logger.debug(response.status_code)
        logger.debug(response.reason)
        logger.debug(response.text)
        logger.debug(response.raw)

        if response.status_code == 200:
            json_data = response.json()
            return json_data

    except Exception as err:
        logger.error(err)
        raise err


if __name__ == '__main__':
    # t = get_new_token()
    # print(t)
    # start = time.perf_counter()
    #
    # point = time.perf_counter()
    # print(point - start)
    date_offset = datetime.datetime.now() - datetime.timedelta(days=1)
    date_offset = date_offset.strftime('%Y-%m-%d')
    transactions = get_um_transactions(search_filter={'status': ['new']})
    print(transactions)
    # print(time.perf_counter() - point)
    #
    # print(transactions)
    for transaction in transactions:
        print(transaction)
        create_at = datetime.datetime.fromisoformat(transaction['createdAt'])
        create_delta = datetime.datetime.now(tz=TZ) - create_at
        print(create_delta > datetime.timedelta(days=2))
    #     create_payment_data_from_new_transaction(transaction)
    #     print()
    # logger.info('test')
    # x = send_transaction_action('1610', 'agent_decline')
    # print(x)