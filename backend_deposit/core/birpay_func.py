import asyncio
import datetime
import os
from pprint import pprint

import requests
import structlog

from backend_deposit.settings import BASE_DIR


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
            logger.info(f'Получено по birpay_id {birpay_id}: {data}')
            print(f'Получено по birpay_id {birpay_id}: {data}')
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

def find_birpay_from_merch_transaction_id(merch_transaction_id, results=1):
    merch_transaction_id = str(merch_transaction_id).strip()
    try:
        token = read_token()
        cookies = None
        headers['Authorization'] = f'Bearer {token}'

        json_data = {
            'filter': {
                'merchantTransactionId': merch_transaction_id
            },
            'sort': {},
            'limit': {
                'lastId': 0,
                'maxResults': results,
                'descending': True,
            },
        }

        response = requests.post(
            'https://birpay-gate.com/api/operator/payout_order/find',
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
                'https://birpay-gate.com/api/operator/payout_order/find',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )

        if response.status_code == 200:
            data = response.json()
            # for key, val in data[0].items():
            #     print(f'{key}: {val}')
            #     print('-------------------\n')
            logger.info(f'Получено по merch_transaction_id {merch_transaction_id}: {data}')
            print(f'Получено по merch_transaction_id {merch_transaction_id}: {data}')
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
            return data
    except Exception as err:
        logger.error(err)
        raise err

async def get_birpay_withdraw(limit=512):
    logger = structlog.getLogger('birgate')
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
            "maxResults": limit,
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
    logger = structlog.getLogger('birgate')
    logger = logger.bind(birpay_withdraw_id=withdraw_id, transaction_id=transaction_id)
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
    logger = structlog.getLogger('birgate')
    logger = logger.bind(birpay_withdraw_id=withdraw_id, transaction_id=transaction_id)
    json_data = {
        "id": withdraw_id,
        "reasonDecline": "err"
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
    #13691648
    # birpay = find_birpay_from_id('45829239')
    # print(birpay)
    t = find_birpay_from_merch_transaction_id(45829239)
    pprint(t)
    t = find_birpay_from_merch_transaction_id(45829236)
    pprint(t)
    # withdraw_list = await get_birpay_withdraw()
    # pprint(withdraw_list)
    # print(len(withdraw_list))
    # ids = "VALUES "
    # wlist = []
    # for w in withdraw_list:
    #     ids += f"('{w['id']}'), "
    #     wlist.append(w['id'])

    # print(ids)
    # withdraw_id = 12131454
    # transaction_id = 655186681
    # res = approve_birpay_withdraw(withdraw_id, transaction_id)
    # print(res)
    # print(wlist)


if __name__ == '__main__':
    asyncio.run(main())