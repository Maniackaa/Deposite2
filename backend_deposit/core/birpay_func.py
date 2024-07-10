import datetime
import time
from pprint import pprint

import requests

from backend_deposit.settings import BASE_DIR
from core.global_func import TZ

def get_new_token():
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

    json_data = {
        'username': 'Operator13_Zajon_AZN',
        'password': 'rPwCrGEE',
    }

    response = requests.post('https://birpay-gate.com/api/login_check', headers=headers, json=json_data)
    if response.status_code == 200:
        token = response.json().get('token')
        with open(BASE_DIR / 'token.txt', 'w') as file:
            file.write(token)
        return token


headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    'Content-Type': 'application/json;charset=utf-8',
    'Referer': 'https://birpay-gate.com/refill-orders/list',
    'Origin': 'https://birpay-gate.com',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Connection': 'keep-alive',
}


def find_birpay_from_id(birpay_id, results=1):
    try:
        token_file = BASE_DIR / 'token.txt'
        if not token_file.exists():
            get_new_token()
        with open(BASE_DIR / 'token.txt', 'r') as file:
            token = file.read()
        cookies = None
        headers['Authorization'] = f'Bearer {token}'

        json_data = {
            'filter': {
                'merchantTransactionId': 655996411
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
        print(response)

        if response.status_code == 401:
            # Обновление токена
            token = get_new_token()
            headers['Authorization']= f'Bearer {token}'
            response = requests.post(
                'https://birpay-gate.com/api/operator/refill_order/find',
                cookies=cookies,
                headers=headers,
                json=json_data,
            )

        if response.status_code == 200:
            print(response.text)
            data = response.json()
            print(data[0])
            # for key, val in data[0].items():
            #     print(f'{key}: {val}')
            #     print('-------------------\n')
            row = data[0]
            pprint(row)

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
            print(result)

            return result
    except Exception as err:
        raise err


if __name__ == '__main__':
    get_new_token()
    birpay = find_birpay_from_id(655996411)
    print(birpay)
