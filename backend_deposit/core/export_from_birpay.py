import time
from datetime import datetime, timezone
from pprint import pprint

import pandas as pd
import requests

from core.birpay_func import get_new_token

token = get_new_token()
token ='eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJpYXQiOjE3NDcwNDU0OTgsImV4cCI6MTc0NzA4MzYwMCwicm9sZXMiOlsiUk9MRV9PUEVSQVRPUiJdLCJ1c2VybmFtZSI6Ik9wZXJhdG9yMTNfWmFqb25fQVpOIn0.i84mryszHMIk3RB2huGs6oVIk7mYl22eAA3J4mTF2qvmp_mahEwDFiM5fIFTxfQab4HXQmE1cSIpHOs3h_mzrWkEjAeUqOXTbRJkY6pEiXvQpDthcnQRNDCl1hLietw7fwYtGZEngue5PSCfJTPIyb7HJJjZ3hNi7y2SsOgZqQ8Zi9f1NbmxRXJBFvRDCoNpe1SVWf2sZ9jDJHIt90C69xS8E6oUr59ws5EgRF-PLl__pWSI00KRAt5HtiHDHDaM506l06E4B3xgvBwHPrr0wHB0UUX5FTfdkt2qMiawDRJZ9zvuNfjbc1ho0btMgpJs0tYBCt2f40eB3f9HN5jDxA'
print(token)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    # 'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/json;charset=utf-8',
    'Origin': 'https://birpay-gate.com',
    'Connection': 'keep-alive',
    'Referer': 'https://birpay-gate.com/refill-orders/list',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Priority': 'u=0',
    # Requests doesn't support trailers
    # 'TE': 'trailers',
}

base_url = 'https://birpay-gate.com/api/operator/refill_order/find'
all_data = []
last_id = 0
max_results = 512
cutoff_date = datetime(2025, 5, 1, tzinfo=timezone.utc)

while True:
    payload = {
        'filter': {
            # 'createdAt': {
            #     'from': '01.05.2025 00:00:00',
            #     'to': '10.05.2025 00:00:00',
            # },
        },
        'sort': {
            'isTrusted': True,
        },
        'limit': {
            'lastId': last_id,
            'maxResults': max_results,
            'descending': True,
        },
    }

    response = requests.post(base_url, headers=headers, json=payload)
    response.raise_for_status()
    result = response.json()
    pprint(result)

    # Проверка структуры: если это список, использовать напрямую
    if isinstance(result, list):
        data = result
    else:
        data = result.get('data', [])

    if not data:
        break

    for entry in data:
        created_at = datetime.fromisoformat(entry['createdAt'].replace("Z", "+00:00"))
        if created_at <= cutoff_date:
            break  # остановка, как только дошли до 1 мая
        all_data.append({
            'id': entry['id'],
            'amount': entry['amount'],
            'status': entry['status'],
            'updatedAt': entry['updatedAt'],
        })

    # если последний элемент уже <= 1 мая, выходим
    if datetime.fromisoformat(data[-1]['createdAt'].replace("Z", "+00:00")) <= cutoff_date:
        break

    last_id = data[-1]['id']
    # time.sleep(0.2)

# Сохраняем в Excel
df = pd.DataFrame(all_data)
df.to_excel("refill_orders_filtered.xlsx", index=False)
print("Сохранено в refill_orders_filtered.xlsx")

