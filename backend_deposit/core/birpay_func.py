import asyncio
import datetime
import os
from pprint import pprint

import requests
import structlog

from backend_deposit.settings import BASE_DIR


logger = structlog.get_logger('deposit')

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
        logger.debug(f'find_birpay_from_id status_code: {response.status_code}')
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
            if data:
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
        logger.error(err, exc_info=True)
        raise err


def get_birpays(results=512) -> dict:
    # Полчение данных по первой таблице birpay
    try:
        token = read_token()
        cookies = None
        headers['Authorization'] = f'Bearer {token}'

        json_data = {
            'filter': {
            },
            'sort': {'isTrusted': True},
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
            logger.info(f'Получено : {len(data)}')
            results = []
            for row in data:
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
                results.append(result)
            return data
    except Exception as err:
        logger.error(err)
        raise err


def find_birpay_from_merch_transaction_id(merch_transaction_id, results=1):
    # это выплаты payout
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


def send_request_birpay(url, method='POST', json_data=None) -> requests.Response:
    token = read_token()
    headers['Authorization'] = f'Bearer {token}'
    if method == "POST":
        response = requests.post(url, headers=headers, json=json_data)
    else:
        response = requests.put(url, headers=headers,json=json_data)

    if response.status_code == 401:
        token = get_new_token()
        headers['Authorization'] = f'Bearer {token}'
        return send_request_birpay(url, method=method, json_data=json_data)
    logger.info(f'response.status_code: {response.status_code}')
    return response

def change_amount_birpay(pk:int, amount:float) -> requests.Response:
    logger.info(f'Меняем birpay_refill {pk} amount: {amount}')
    url = 'https://birpay-gate.com/api/operator/refill_order/change/amount'
    method='PUT'
    json_data = {
        'id': pk,
        'amount': amount,
    }
    response = send_request_birpay(url=url, method=method, json_data=json_data)
    logger.info(f'birpay_refill {pk}  amount: {amount} response.status_code: {response.status_code}. response.text: {response.text}')
    return response

def approve_birpay_refill(pk:int) -> requests.Response:
    logger.info(f'Подтверждаем birpay_refill: {pk}')
    url = 'https://birpay-gate.com/api/operator/refill_order/approve'
    method='PUT'
    json_data = {
        'id': pk,
    }
    response = send_request_birpay(url=url, method=method, json_data=json_data)
    logger.info(f'birpay_refill: {pk} response.status_code: {response.status_code}. response.text: {response.text}')
    return response

# ------------------- ВТОРАЯ ТАБЛИЦА ------------------
async def get_birpay_withdraw(limit=512):
    logger = structlog.get_logger('deposit')
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
    logger = structlog.get_logger('deposit')
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
    logger = structlog.get_logger('deposit')
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
    # birpay = find_birpay_from_id('888308930')
    # print(birpay)
    # t = find_birpay_from_merch_transaction_id(45829239)
    # pprint(t)
    # t = find_birpay_from_merch_transaction_id('889136356')
    # pprint(t)
    # withdraw_list = await get_birpay_withdraw(limit=10)
    # pprint(withdraw_list)
    r = change_amount_birpay(pk=888308930, amount=1)
    print(r.status_code)
    print(r.text)
    r = approve_birpay_refill(pk=888308930)
    print(r.status_code)
    print(r.text)
    # print(len(withdraw_list))
    # ids = "VALUES "
    # wlist = []
    # for w in withdraw_list:
    #     ids += f"('{w['id']}'), "
    #     wlist.append(w['id'])

    # print(ids)
    # withdraw_id = 13864000
    # transaction_id = 755119964
    # res = approve_birpay_withdraw(withdraw_id, transaction_id)
    # print(res)
    # print(wlist)


if __name__ == '__main__':
    asyncio.run(main())