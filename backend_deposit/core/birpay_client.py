"""
Фасад для работы с Birpay API.
- Реквизиты: получение списка, обновление (номер карты), переключение активности.
- Refill (пополнение): список заявок, поиск по merchant_transaction_id, смена суммы, подтверждение.
- Payout (выплаты): список заявок, поиск по merchant_transaction_id, подтверждение, отклонение.
Учётные данные и хост берутся из Options (birpay_host, birpay_login, birpay_password)
или из переменных окружения BIRPAY_*.
"""
import os
from typing import Any

import requests
import structlog

logger = structlog.get_logger('deposit')

DEFAULT_BIRPAY_BASE_URL = 'https://birpay-gate.com'


def _default_headers(base_url: str) -> dict:
    origin = base_url.rstrip('/')
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
        'Content-Type': 'application/json;charset=utf-8',
        'Origin': origin,
        'Connection': 'keep-alive',
        'Referer': f'{origin}/login',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }


class BirpayClient:
    """
    Клиент Birpay API (реквизиты и др.).
    Хост и логин/пароль — из Options или env.
    """

    def __init__(
        self,
        base_url: str | None = None,
        login: str | None = None,
        password: str | None = None,
        token_file_path: str | None = None,
    ):
        if base_url is None or login is None or password is None:
            try:
                from users.models import Options
                opts = Options.load()
                base_url = base_url or getattr(opts, 'birpay_host', None) or os.getenv('BIRPAY_HOST') or DEFAULT_BIRPAY_BASE_URL
                login = login or getattr(opts, 'birpay_login', None) or os.getenv('BIRPAY_LOGIN', '')
                password = password or getattr(opts, 'birpay_password', None) or os.getenv('BIRPAY_PASSWORD', '')
            except Exception:
                base_url = base_url or os.getenv('BIRPAY_HOST') or DEFAULT_BIRPAY_BASE_URL
                login = login or os.getenv('BIRPAY_LOGIN', '')
                password = password or os.getenv('BIRPAY_PASSWORD', '')

        self.base_url = base_url.rstrip('/')
        self.login = login
        self.password = password
        self._token = None
        self._token_file = token_file_path
        self._headers = _default_headers(self.base_url)

    def _token_file_path(self):
        if self._token_file:
            return self._token_file
        from django.conf import settings
        return getattr(settings, 'BASE_DIR', None) and settings.BASE_DIR / 'token.txt' or 'token.txt'

    def get_token(self) -> str:
        """Получить токен (из файла или логин/пароль)."""
        path = self._token_file_path()
        if hasattr(path, 'exists') and path.exists():
            try:
                with open(path, 'r') as f:
                    token = f.read().strip()
                    if token:
                        return token
            except Exception:
                pass
        return self.refresh_token()

    def refresh_token(self) -> str:
        """Получить новый токен по логину/паролю."""
        url = f'{self.base_url}/api/login_check'
        resp = requests.post(
            url,
            headers={**self._headers, 'Content-Type': 'application/json'},
            json={'username': self.login, 'password': self.password},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning('Birpay login failed', status_code=resp.status_code, url=url)
            raise RuntimeError(f'Birpay login failed: {resp.status_code}')
        data = resp.json()
        token = data.get('token') or data.get('access')
        if not token:
            raise RuntimeError('Birpay response has no token')
        path = self._token_file_path()
        try:
            with open(path, 'w') as f:
                f.write(token)
        except Exception as e:
            logger.warning('Could not save Birpay token file', path=str(path), error=str(e))
        self._token = token
        return token

    def _request(self, method: str, path: str, json_data: dict | None = None) -> requests.Response:
        """Выполнить запрос к API (с обновлением токена при 401)."""
        url = f'{self.base_url}{path}' if path.startswith('/') else f'{self.base_url}/api/operator/{path}'
        token = self.get_token()
        headers = {**self._headers, 'Authorization': f'Bearer {token}'}
        logger.debug(f'url: {url}; {method} json: {json_data}')
        if method.upper() == 'POST':
            resp = requests.post(url, headers=headers, json=json_data or {}, timeout=10)
        else:
            resp = requests.put(url, headers=headers, json=json_data or {}, timeout=10)
        if resp.status_code == 401:
            token = self.refresh_token()
            headers['Authorization'] = f'Bearer {token}'
            if method.upper() == 'POST':
                resp = requests.post(url, headers=headers, json=json_data or {}, timeout=10)
            else:
                resp = requests.put(url, headers=headers, json=json_data or {}, timeout=10)
        return resp

    # --- Реквизиты (payment_requisite) ---

    def get_requisites(self) -> list:
        """
        Получить все реквизиты (payment_requisite/find).
        Returns:
            list: список объектов реквизитов из ответа Birpay.
        """
        json_data = {
            'filter': {},
            'sort': {'isTrusted': True},
            'limit': {'lastId': 0, 'maxResults': 512, 'descending': False},
        }
        resp = self._request('POST', '/api/operator/payment_requisite/find', json_data=json_data)
        logger.debug('Birpay get_requisites', status_code=resp.status_code)
        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise RuntimeError(f'Birpay get_requisites failed: {resp.status_code} {err}')
        return resp.json()

    def update_requisite(
        self,
        requisite_id: int,
        *,
        name: str,
        agent_id: int,
        weight: int,
        card_number: str,
        active: bool | None = None,
        refill_method_types: list | None = None,
        users: list | None = None,
        payment_requisite_filter_id: int | None = None,
        full_payload: dict | None = None,
    ) -> dict[str, Any]:
        """
        Обновить реквизит (номер карты и др.).
        card_number — сырое значение для Birpay (payload.card_number).
        """
        refill_method_types = refill_method_types or []
        users = users or []
        users_ids = []
        for u in users:
            if isinstance(u, dict):
                uid = u.get('id')
                if uid is not None:
                    users_ids.append({'id': uid})
            elif isinstance(u, (int, str)):
                users_ids.append({'id': int(u)})

        if full_payload is not None:
            payload_data = dict(full_payload)
            payload_data['card_number'] = card_number
        else:
            payload_data = {'card_number': card_number}

        json_data = {
            'id': requisite_id,
            'name': name,
            'weight': weight,
            'refillMethodTypes': refill_method_types,
            'agent': {'id': agent_id},
            'payload': payload_data,
            'users': users_ids,
        }
        if active is not None:
            json_data['active'] = active
        if payment_requisite_filter_id is not None:
            json_data['paymentRequisiteFilterId'] = payment_requisite_filter_id

        log = logger.bind(requisite_id=requisite_id)
        log.info(
            'Birpay update_requisite: отправка',
            card_number_len=len(card_number) if card_number else 0,
            refill_method_types_count=len(refill_method_types),
            users_ids=users_ids,
            agent_id=agent_id,
        )
        resp = self._request('PUT', '/api/operator/payment_requisite', json_data=json_data)
        try:
            response_json = resp.json()
        except ValueError:
            response_json = {'raw': resp.text[:500]}
            log.warning('Birpay update_requisite: ответ не JSON', raw=resp.text[:200])
        success = resp.status_code in (200, 201)
        log.info(
            'Birpay update_requisite: ответ',
            status_code=resp.status_code,
            success=success,
        )
        if not success:
            log.warning(
                'Birpay update_requisite: ошибка Birpay',
                status_code=resp.status_code,
                code=response_json.get('code') if isinstance(response_json, dict) else None,
                message=response_json.get('message') if isinstance(response_json, dict) else None,
                data=response_json,
            )
        result = {
            'status_code': resp.status_code,
            'data': response_json,
            'success': success,
        }
        if not success:
            result['error'] = response_json.get('error') or response_json.get('detail') or str(response_json)
        return result

    def set_requisite_active(
        self,
        requisite_id: int,
        active: bool,
        *,
        name: str,
        agent_id: int,
        weight: int,
        card_number: str,
        refill_method_types: list | None = None,
        users: list | None = None,
        payment_requisite_filter_id: int | None = None,
    ) -> dict[str, Any]:
        """Переключить активность реквизита."""
        return self.update_requisite(
            requisite_id,
            name=name,
            agent_id=agent_id,
            weight=weight,
            card_number=card_number,
            active=active,
            refill_method_types=refill_method_types,
            users=users,
            payment_requisite_filter_id=payment_requisite_filter_id,
        )

    # --- Refill (пополнение) ---

    def get_refill_orders(self, limit: int = 512) -> list:
        """
        Получить список заявок на пополнение (refill_order/find).
        Returns:
            list: сырые объекты заявок из ответа API.
        """
        json_data = {
            'filter': {},
            'sort': {'isTrusted': True},
            'limit': {'lastId': 0, 'maxResults': limit, 'descending': True},
        }
        resp = self._request('POST', '/api/operator/refill_order/find', json_data=json_data)
        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise RuntimeError(f'Birpay get_refill_orders failed: {resp.status_code} {err}')
        data = resp.json()
        logger.info('Birpay get_refill_orders', count=len(data) if isinstance(data, list) else 0)
        return data if isinstance(data, list) else []

    def find_refill_order(self, merchant_transaction_id: str, results: int = 1) -> dict | None:
        """
        Поиск заявки пополнения по Merchant Transaction ID.
        Returns:
            Первый объект заявки или None.
        """
        merchant_transaction_id = str(merchant_transaction_id).strip()
        if not merchant_transaction_id:
            return None
        json_data = {
            'filter': {'merchantTransactionId': merchant_transaction_id},
            'sort': {},
            'limit': {'lastId': 0, 'maxResults': results, 'descending': True},
        }
        resp = self._request('POST', '/api/operator/refill_order/find', json_data=json_data)
        if resp.status_code != 200:
            logger.warning('Birpay find_refill_order', status_code=resp.status_code)
            return None
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None

    def change_refill_amount(self, refill_id: int, amount: float) -> requests.Response:
        """Изменить сумму заявки на пополнение (refill_order/change/amount)."""
        logger.info('Birpay change_refill_amount', refill_id=refill_id, amount=amount)
        json_data = {'id': refill_id, 'amount': amount}
        resp = self._request('PUT', '/api/operator/refill_order/change/amount', json_data=json_data)
        logger.info('Birpay change_refill_amount result', status_code=resp.status_code)
        return resp

    def approve_refill(self, refill_id: int) -> requests.Response:
        """Подтвердить заявку на пополнение (refill_order/approve)."""
        logger.info('Birpay approve_refill', refill_id=refill_id)
        json_data = {'id': refill_id}
        resp = self._request('PUT', '/api/operator/refill_order/approve', json_data=json_data)
        logger.info('Birpay approve_refill result', status_code=resp.status_code)
        return resp

    # --- Payout (выплаты) ---

    def get_payout_orders(
        self,
        limit: int = 512,
        status_filter: list[int] | None = None,
    ) -> list:
        """
        Получить список заявок на выплату (payout_order/find).
        status_filter: например [0] для статуса «в ожидании». По умолчанию [0].
        """
        if status_filter is None:
            status_filter = [0]
        json_data = {
            'filter': {'status': status_filter},
            'sort': {'isTrusted': True},
            'limit': {'lastId': 0, 'maxResults': limit, 'descending': True},
        }
        resp = self._request('POST', '/api/operator/payout_order/find', json_data=json_data)
        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:
                err = resp.text
            raise RuntimeError(f'Birpay get_payout_orders failed: {resp.status_code} {err}')
        data = resp.json()
        return data if isinstance(data, list) else []

    def find_payout_order(self, merchant_transaction_id: str, results: int = 1) -> dict | None:
        """
        Поиск заявки выплаты по Merchant Transaction ID.
        Returns:
            Первый объект заявки или None.
        """
        merchant_transaction_id = str(merchant_transaction_id).strip()
        if not merchant_transaction_id:
            return None
        json_data = {
            'filter': {'merchantTransactionId': merchant_transaction_id},
            'sort': {},
            'limit': {'lastId': 0, 'maxResults': results, 'descending': True},
        }
        resp = self._request('POST', '/api/operator/payout_order/find', json_data=json_data)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None

    def approve_payout(self, withdraw_id: int, operator_transaction_id: int | str) -> dict[str, Any]:
        """Подтвердить заявку на выплату (payout_order/approve)."""
        logger.info('Birpay approve_payout', withdraw_id=withdraw_id, operator_transaction_id=operator_transaction_id)
        json_data = {
            'id': withdraw_id,
            'operatorTransactionId': str(operator_transaction_id),
        }
        resp = self._request('PUT', '/api/operator/payout_order/approve', json_data=json_data)
        try:
            result = resp.json()
        except ValueError:
            result = {'raw': resp.text[:500]}
        logger.debug('Birpay approve_payout result', status_code=resp.status_code, result=result)
        return result

    def decline_payout(self, withdraw_id: int, reason: str = 'err') -> dict[str, Any]:
        """Отклонить заявку на выплату (payout_order/decline)."""
        logger.info('Birpay decline_payout', withdraw_id=withdraw_id)
        json_data = {'id': withdraw_id, 'reasonDecline': reason}
        resp = self._request('PUT', '/api/operator/payout_order/decline', json_data=json_data)
        try:
            result = resp.json()
        except ValueError:
            result = {'raw': resp.text[:500]}
        logger.debug('Birpay decline_payout result', status_code=resp.status_code, result=result)
        return result
