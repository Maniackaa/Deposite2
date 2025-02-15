import asyncio
import datetime
import os
from pprint import pprint

import requests
import structlog
from django.conf import settings

from backend_deposit.settings import BASE_DIR
from core.asu_pay_func import get_asu_token, get_new_asu_token
from core.global_func import hash_gen
from users.models import Options

logger = structlog.get_logger(__name__)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    'Content-Type': 'application/json;charset=utf-8',
    'Origin': 'https://birpay-gate.com',
    'Connection': 'keep-alive',
    'Referer': 'https://birpay-gate.com/login',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
}

def get_new_token(username=os.getenv('BIRPAY_LOGIN'), password=os.getenv('BIRPAY_PASSWORD')):
    # headers = {
    #     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0',
    #     'Accept': 'application/json, text/plain, */*',
    #     'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    #     'Content-Type': 'application/json;charset=utf-8',
    #     'Origin': 'https://birpay-gate.com',
    #     'Connection': 'keep-alive',
    #     'Referer': 'https://birpay-gate.com/login',
    #     'Sec-Fetch-Dest': 'empty',
    #     'Sec-Fetch-Mode': 'cors',
    #     'Sec-Fetch-Site': 'same-origin',
    # }

    json_data = {
        'username': username,
        'password': password,
    }

    response = requests.post('https://birpay-gate.com/api/login_check', headers=headers, json=json_data)
    if response.status_code == 200:
        token = response.json().get('token')
        with open(BASE_DIR / 'token.txt', 'w') as file:
            file.write(token)
        return token



def read_token():
    token_file = BASE_DIR / 'token.txt'
    if not token_file.exists():
        get_new_token()
    with open(BASE_DIR / 'token.txt', 'r') as file:
        token = file.read()
        return token

def find_birpay_from_id(birpay_id, results=1):
    birpay_id = str(birpay_id).strip()
    try:
        token = read_token()
        cookies = None
        headers['Authorization'] = f'Bearer {token}'

        json_data = {
            'filter': {
                'merchantTransactionId': birpay_id
            },
            'sort': {},
            'limit': {
                'lastId': 0,
                'maxResults': results,
                'descending': True,
            },
        }

        response = requests.post(
            'https://birpay-gate.com/api/operator/refill_order/find',
            cookies=cookies,
            headers=headers,
            json=json_data,
        )
        logger.debug(f'find_birpay_from_id: {response.status_code}')
        if response.status_code == 401:
            # Обновление токена
            token = get_new_token()
            headers['Authorization'] = f'Bearer {token}'
            response = requests.post(
                'https://birpay-gate.com/api/operator/refill_order/find',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )

        if response.status_code == 200:
            data = response.json()
            # for key, val in data[0].items():
            #     print(f'{key}: {val}')
            #     print('-------------------\n')
            logger.debug(f'Получено по birpay_id {birpay_id}: {data}')

            row = data[0]
            transaction_id = row.get('merchantTransactionId')
            status = row.get('status')
            sender = row.get('customerWalletId')
            requisite = row.get('paymentRequisite')
            pay = float(row.get('amount'))
            operator = row.get('operator')
            created_time = datetime.datetime.fromisoformat(row.get('createdAt'))
            result = {
                    'transaction_id': transaction_id,
                    'status': status,
                    'sender': sender,
                    'requisite': requisite,
                    'created_time': created_time,
                    'pay': pay,
                    'operator': operator,
                }
            return result
    except Exception as err:
        logger.error(err)
        raise err

async def get_birpay_withdraw():
    token = read_token()
    headers['Authorization'] = f'Bearer {token}'

    json_data = {
        "filter": {
            "status": [
                0,
            ],
        },
        "sort": {
            "isTrusted": True,
        },
        "limit": {
            "lastId": 0,
            "maxResults": 100,
            "descending": True,
        },
    }

    response = requests.post('https://birpay-gate.com/api/operator/payout_order/find', headers=headers, json=json_data)
    logger.debug(f'response.status_code: {response.status_code}')
    if response.status_code == 401:
        # Обновление токена
        token = get_new_token()
        headers['Authorization'] = f'Bearer {token}'
    response = requests.post('https://birpay-gate.com/api/operator/payout_order/find', headers=headers, json=json_data)
    result = response.json()
    return result


def approve_birpay_withdraw(withdraw_id, transaction_id):
    json_data = {
        "id": withdraw_id,
        "operatorTransactionId": transaction_id,
    }
    token = read_token()
    headers['Authorization'] = f'Bearer {token}'
    response = requests.put('https://birpay-gate.com/api/operator/payout_order/approve', headers=headers, json=json_data)
    if response.status_code == 401:
        # Обновление токена
        token = get_new_token()
        headers['Authorization'] = f'Bearer {token}'
    response = requests.put('https://birpay-gate.com/api/operator/payout_order/approve', headers=headers, json=json_data)
    result = response.json()
    logger.debug(f'approve_birpay_withdraw {withdraw_id} {transaction_id}: {response.status_code}. result: {result}')
    return result


def decline_birpay_withdraw(withdraw_id, transaction_id):
    json_data = {
        'id': withdraw_id,
        'operatorTransactionId': transaction_id,
    }
    token = read_token()
    headers['Authorization'] = f'Bearer {token}'
    response = requests.put('https://birpay-gate.com/api/operator/payout_order/decline', headers=headers, json=json_data)
    if response.status_code == 401:
        # Обновление токена
        token = get_new_token()
        headers['Authorization'] = f'Bearer {token}'
    response = requests.put('https://birpay-gate.com/api/operator/payout_order/decline', headers=headers, json=json_data)
    result = response.json()
    logger.debug(f'decline_birpay_withdraw {withdraw_id} {transaction_id}: {response.status_code}. result: {result}')
    return result

async def main():
    token = get_new_token()
    print(token)
    # birpay = find_birpay_from_id('710021863')
    withdraw_list = await get_birpay_withdraw()
    pprint(withdraw_list)
    print(len(withdraw_list))
    total_amount = 0

    for withdraw in withdraw_list[:1]:
        expired_month = expired_year = target_phone = card_data = None
        # print(withdraw)
        amount = float(withdraw.get('amount'))
        total_amount += amount
        wallet_id = withdraw.get('customerWalletId', '')
        if wallet_id.startswith('994'):
            target_phone = f'+{wallet_id}'
        else:
            payload = withdraw.get('payload', {})
            if payload:
                card_date = payload.get('card_date')
                if card_date:
                    expired_month, expired_year = card_date.split('/')
                    if expired_year:
                        expired_year = expired_year[-2:]
            card_data = {
                "card_number": wallet_id,
                "expired_month": expired_month,
                "expired_year": expired_year
            }
        print(withdraw['id'], amount, card_data, target_phone)
        # await create_asu_withdraw(withdraw_id=withdraw['id'], amount=amount, card_data=card_data, target_phone=target_phone)
        print()
    print(total_amount)

if __name__ == '__main__':
    asyncio.run(main())