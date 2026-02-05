"""
REST API (DRF) для работы с Birpay через класс BirpayClient.
Используется сервисом Депозит; проект ASU обращается к этому API, а не к Birpay напрямую.
При обновлении реквизита используется единый сервис update_requisite_on_birpay (ID + overrides).
Аутентификация: только JWT (логин/пароль в SupportOptions на ASU → POST /api/token/ Депозита) или сессия Депозита; пользователь должен быть staff или superuser.
"""
import structlog
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.birpay_client import BirpayClient
from deposit.birpay_requisite_service import update_requisite_on_birpay
from deposit.models import RequsiteZajon

logger = structlog.get_logger('deposit')


class BirpayAPIPermission(IsAdminUser):
    """Доступ: аутентифицированный пользователь Депозита с is_staff или is_superuser (JWT от ASU или сессия)."""
    def has_permission(self, request, view):
        return (
            request.user
            and getattr(request.user, 'is_authenticated', False)
            and (getattr(request.user, 'is_superuser', False) or getattr(request.user, 'is_staff', False))
        )


# --- Реквизиты ---

class BirpayRequisitesListAPIView(APIView):
    """GET /api/birpay/requisites/ — список реквизитов Birpay с полями из БД (card_number, works_on_asu)."""
    permission_classes = [BirpayAPIPermission]

    def get(self, request: Request):
        try:
            data = BirpayClient().get_requisites()
        except Exception as e:
            logger.exception('Birpay API get_requisites failed')
            return Response(
                {'error': str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        if not data:
            return Response(data)
        ids = []
        for item in data:
            rid = item.get('id') if isinstance(item, dict) else getattr(item, 'id', None)
            if rid is not None:
                ids.append(int(rid))
        db_by_id = {}
        if ids:
            for req in RequsiteZajon.objects.filter(pk__in=ids).values('id', 'card_number', 'works_on_asu', 'payload'):
                payload = req.get('payload') or {}
                raw_card = (payload.get('card_number') or req.get('card_number') or '').strip()
                db_by_id[req['id']] = {
                    'card_number': req['card_number'] or '',
                    'raw_card_number': raw_card,
                    'works_on_asu': bool(req['works_on_asu']),
                }
        result = []
        for item in data:
            row = dict(item) if isinstance(item, dict) else dict(getattr(item, '__dict__', item))
            rid = row.get('id')
            if rid is not None:
                rid = int(rid)
            db_row = db_by_id.get(rid) if rid is not None else None
            row['card_number'] = db_row['card_number'] if db_row else ''
            row['raw_card_number'] = db_row['raw_card_number'] if db_row else ''
            row['works_on_asu'] = db_row['works_on_asu'] if db_row else False
            result.append(row)
        return Response(result)


class BirpayRequisiteUpdateAPIView(APIView):
    """
    PUT /api/birpay/requisites/<id>/ — обновить реквизит.
    Body: изменяемые поля (card_number и т.д.) и опционально changed_by_user_id, changed_by_username (агент ASU).
    После успеха Birpay обновляется локальная модель и пишется лог с данными агента.
    """
    permission_classes = [BirpayAPIPermission]

    def put(self, request: Request, requisite_id: int):
        body = dict(request.data or {})
        changed_by_user_id = body.pop('changed_by_user_id', None)
        changed_by_username = body.pop('changed_by_username', None)
        if changed_by_user_id is not None:
            try:
                changed_by_user_id = int(changed_by_user_id)
            except (TypeError, ValueError):
                changed_by_user_id = None
        if changed_by_username is not None:
            changed_by_username = str(changed_by_username).strip() or None
        log_ctx = logger.bind(requisite_id=requisite_id, birpay_api='update_requisite')
        log_ctx.info('Birpay API update_requisite: вызов сервиса', body_keys=list(body.keys()))
        try:
            result = update_requisite_on_birpay(requisite_id, body)
        except Http404:
            return Response({'error': f'Реквизит {requisite_id} не найден в модели.'}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            log_ctx.warning('Birpay API update_requisite: ошибка валидации', error=str(e))
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            log_ctx.exception('Birpay API update_requisite failed', error=str(e))
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        success = result.get('success')
        status_code = result.get('status_code')
        log_ctx.info(
            'Birpay API update_requisite: ответ Birpay',
            success=success,
            status_code=status_code,
            error=result.get('error'),
        )
        if not success:
            log_ctx.warning(
                'Birpay API update_requisite: Birpay вернул ошибку',
                status_code=status_code,
                data=result.get('data'),
                error=result.get('error'),
            )
        if success and body:
            try:
                requisite = RequsiteZajon.objects.get(pk=requisite_id)
                update_fields = []
                if 'card_number' in body:
                    raw = (body.get('card_number') or '').strip()
                    requisite.card_number = (raw.replace(' ', '').replace('-', '')[:16]) if raw else ''
                    payload = dict(requisite.payload or {})
                    payload['card_number'] = raw
                    requisite.payload = payload
                    update_fields.extend(['card_number', 'payload'])
                if update_fields:
                    requisite._change_source = 'api'
                    requisite._changed_by_user_id = changed_by_user_id
                    requisite._changed_by_username = changed_by_username or ''
                    requisite.save(update_fields=update_fields)
            except RequsiteZajon.DoesNotExist:
                pass
            except Exception as e:
                log_ctx.exception('Birpay API update_requisite: не удалось обновить локальную модель', error=str(e))
        return Response(result, status=status.HTTP_200_OK if success else status.HTTP_502_BAD_GATEWAY)


class BirpayRequisiteSetActiveAPIView(APIView):
    """POST /api/birpay/requisites/<id>/set-active/ — включить/выключить реквизит. Body: {"active": true|false}, опционально changed_by_user_id, changed_by_username."""
    permission_classes = [BirpayAPIPermission]

    def post(self, request: Request, requisite_id: int):
        body = dict(request.data or {})
        active = body.get('active')
        if active is None:
            return Response({'error': 'Required: active (boolean)'}, status=status.HTTP_400_BAD_REQUEST)
        changed_by_user_id = body.pop('changed_by_user_id', None)
        changed_by_username = body.pop('changed_by_username', None)
        if changed_by_user_id is not None:
            try:
                changed_by_user_id = int(changed_by_user_id)
            except (TypeError, ValueError):
                changed_by_user_id = None
        if changed_by_username is not None:
            changed_by_username = str(changed_by_username).strip() or None
        try:
            result = update_requisite_on_birpay(requisite_id, {'active': bool(active)})
        except Http404:
            return Response({'error': f'Реквизит {requisite_id} не найден.'}, status=status.HTTP_404_NOT_FOUND)
        if result.get('success'):
            try:
                requisite = RequsiteZajon.objects.get(pk=requisite_id)
                requisite.active = bool(active)
                requisite._change_source = 'api'
                requisite._changed_by_user_id = changed_by_user_id
                requisite._changed_by_username = changed_by_username or ''
                requisite.save(update_fields=['active'])
            except RequsiteZajon.DoesNotExist:
                pass
        return Response(result, status=status.HTTP_200_OK if result.get('success') else status.HTTP_502_BAD_GATEWAY)


# --- Refill (пополнение) ---

class BirpayRefillOrdersListAPIView(APIView):
    """GET /api/birpay/refill-orders/?limit=512 — список заявок на пополнение."""
    permission_classes = [BirpayAPIPermission]

    def get(self, request: Request):
        limit = int(request.query_params.get('limit', 512))
        try:
            data = BirpayClient().get_refill_orders(limit=limit)
            return Response(data)
        except Exception as e:
            logger.exception('Birpay API get_refill_orders failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


class BirpayRefillOrderFindAPIView(APIView):
    """GET /api/birpay/refill-orders/find/?merchant_transaction_id= — поиск заявки пополнения по Merchant Tx ID."""
    permission_classes = [BirpayAPIPermission]

    def get(self, request: Request):
        mtx_id = (request.query_params.get('merchant_transaction_id') or '').strip()
        if not mtx_id:
            return Response({'error': 'Required: merchant_transaction_id'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            data = BirpayClient().find_refill_order(mtx_id)
            if data is None:
                return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
            return Response(data)
        except Exception as e:
            logger.exception('Birpay API find_refill_order failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


class BirpayRefillOrderAmountAPIView(APIView):
    """PUT /api/birpay/refill-orders/<id>/amount/ — изменить сумму заявки. Body: {"amount": 100.0}."""
    permission_classes = [BirpayAPIPermission]

    def put(self, request: Request, refill_id: int):
        amount = request.data.get('amount')
        if amount is None:
            return Response({'error': 'Required: amount'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            resp = BirpayClient().change_refill_amount(refill_id, float(amount))
            if resp.status_code == 200:
                try:
                    return Response(resp.json())
                except Exception:
                    return Response({'status_code': resp.status_code})
            return Response(
                {'error': resp.text, 'status_code': resp.status_code},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception('Birpay API change_refill_amount failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


class BirpayRefillOrderApproveAPIView(APIView):
    """PUT /api/birpay/refill-orders/<id>/approve/ — подтвердить заявку на пополнение."""
    permission_classes = [BirpayAPIPermission]

    def put(self, request: Request, refill_id: int):
        try:
            resp = BirpayClient().approve_refill(refill_id)
            if resp.status_code == 200:
                try:
                    return Response(resp.json() if resp.content else {'ok': True})
                except Exception:
                    return Response({'status_code': resp.status_code})
            return Response(
                {'error': resp.text, 'status_code': resp.status_code},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception('Birpay API approve_refill failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


# --- Payout (выплаты) ---

class BirpayPayoutOrdersListAPIView(APIView):
    """GET /api/birpay/payout-orders/?limit=512&status=0 — список заявок на выплату."""
    permission_classes = [BirpayAPIPermission]

    def get(self, request: Request):
        limit = int(request.query_params.get('limit', 512))
        status_param = request.query_params.get('status', '0')
        try:
            status_filter = [int(x) for x in status_param.split(',') if x.strip()]
        except ValueError:
            status_filter = [0]
        if not status_filter:
            status_filter = [0]
        try:
            data = BirpayClient().get_payout_orders(limit=limit, status_filter=status_filter)
            return Response(data)
        except Exception as e:
            logger.exception('Birpay API get_payout_orders failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


class BirpayPayoutOrderFindAPIView(APIView):
    """GET /api/birpay/payout-orders/find/?merchant_transaction_id= — поиск заявки выплаты по Merchant Tx ID."""
    permission_classes = [BirpayAPIPermission]

    def get(self, request: Request):
        mtx_id = (request.query_params.get('merchant_transaction_id') or '').strip()
        if not mtx_id:
            return Response({'error': 'Required: merchant_transaction_id'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            data = BirpayClient().find_payout_order(mtx_id)
            if data is None:
                return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
            return Response(data)
        except Exception as e:
            logger.exception('Birpay API find_payout_order failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


class BirpayPayoutOrderApproveAPIView(APIView):
    """PUT /api/birpay/payout-orders/<id>/approve/ — подтвердить выплату. Body: {"operator_transaction_id": "..."}."""
    permission_classes = [BirpayAPIPermission]

    def put(self, request: Request, withdraw_id: int):
        operator_tx_id = request.data.get('operator_transaction_id')
        if operator_tx_id is None:
            return Response({'error': 'Required: operator_transaction_id'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = BirpayClient().approve_payout(withdraw_id, operator_tx_id)
            return Response(result)
        except Exception as e:
            logger.exception('Birpay API approve_payout failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)


class BirpayPayoutOrderDeclineAPIView(APIView):
    """PUT /api/birpay/payout-orders/<id>/decline/ — отклонить выплату. Body: {"reason": "err"} (optional)."""
    permission_classes = [BirpayAPIPermission]

    def put(self, request: Request, withdraw_id: int):
        reason = (request.data or {}).get('reason', 'err')
        try:
            result = BirpayClient().decline_payout(withdraw_id, reason=str(reason))
            return Response(result)
        except Exception as e:
            logger.exception('Birpay API decline_payout failed')
            return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)
