"""
Тесты предотвращения двойного создания Payment на ASU (Z-ASU).

Причина двойного создания: refresh_birpay_data вызывается по расписанию (например каждые N сек).
Один и тот же BirpayOrder мог обрабатываться дважды: первый раз — отправка на ASU и сохранение payment_id;
второй раз (через ~5 сек) — условие «нет payment_id» ещё выполнялось (или два воркера параллельно),
и запрос на create-payment уходил повторно.

Защита: 1) В Deposit перед отправкой перечитываем order из БД (refresh_from_db) и не шлём, если уже есть payment_id.
         2) В Payment (ASU) при create-payment проверяем дубликат по (merchant Z-ASU, order_id) и возвращаем существующий Payment.
         3) ASU возвращает строго 201 при создании, 200 при идемпотентности; Deposit только 201 считает созданием заявки.

Запуск тестов (обязательно с активированным окружением venv):
  Из корня проекта:  venv\\Scripts\\python -m pytest backend_deposit/deposit/tests/test_z_asu_no_duplicate_send.py -v
  Или:  cd backend_deposit && ..\\venv\\Scripts\\python manage.py test deposit.tests.test_z_asu_no_duplicate_send -v 2
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone

from deposit.models import BirpayOrder, RequsiteZajon
from deposit.tasks import process_birpay_order


def _birpay_data(birpay_id=5001, merchant_tx_id='mtx-5001', card_number='4111111111111111', amount=100.0):
    """Минимальные данные от Birpay API для process_birpay_order."""
    now = timezone.now()
    return {
        'id': birpay_id,
        'merchantTransactionId': merchant_tx_id,
        'createdAt': now.isoformat(),
        'updatedAt': now.isoformat(),
        'merchantUserId': 'user1',
        'merchant': {'name': 'Test'},
        'customerName': 'Customer',
        'amount': str(amount),
        'status': 0,
        'payload': {},
        'paymentRequisite': {'payload': {'card_number': card_number}},
    }


class TestZASUNoDuplicateSend(TestCase):
    """Проверка: отправка на ASU не дублируется при повторном вызове process_birpay_order."""

    def setUp(self):
        self.now = timezone.now()
        RequsiteZajon.objects.create(
            id=9001,
            active=True,
            agent_id=1,
            agent_name='Test Agent',
            name='Test Z-ASU requisite',
            weight=0,
            created_at=self.now,
            updated_at=self.now,
            card_number='4111111111111111',
            works_on_asu=True,
        )

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_order_with_payment_id_does_not_send_to_z_asu(self, mock_send):
        """Если у заявки уже есть payment_id, send_birpay_order_to_z_asu не вызывается."""
        order = BirpayOrder.objects.create(
            birpay_id=5001,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-5001',
            merchant_user_id='user1',
            card_number='4111111111111111',
            status=0,
            status_internal=0,
            amount=100.0,
            operator='op',
            raw_data={},
            payment_id='existing-uuid-from-first-call',
        )
        data = _birpay_data(birpay_id=5001, merchant_tx_id='mtx-5001')
        process_birpay_order(data)
        mock_send.assert_not_called()

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_process_birpay_order_twice_sends_only_once(self, mock_send):
        """Два вызова process_birpay_order с одними данными — отправка на ASU только один раз."""
        mock_send.return_value = {'success': True, 'payment_id': 'uuid-from-asu-123'}
        data = _birpay_data(birpay_id=5002, merchant_tx_id='mtx-5002')

        process_birpay_order(data)
        self.assertEqual(mock_send.call_count, 1)
        order = BirpayOrder.objects.get(birpay_id=5002)
        self.assertEqual(order.payment_id, 'uuid-from-asu-123')

        process_birpay_order(data)
        self.assertEqual(mock_send.call_count, 1, 'Второй вызов не должен вызывать send (payment_id уже сохранён)')

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_refresh_from_db_called_before_z_asu_send(self, mock_send):
        """Перед решением «отправлять ли на ASU» вызывается refresh_from_db(fields=['payment_id']) — защита от двойной отправки."""
        mock_send.return_value = {'success': True, 'payment_id': 'uuid-123'}
        data = _birpay_data(birpay_id=5003, merchant_tx_id='mtx-5003')
        original_refresh = BirpayOrder.refresh_from_db
        call_tracker = []

        def tracking_refresh(self, *args, **kwargs):
            call_tracker.append(kwargs.copy())
            return original_refresh(self, *args, **kwargs)

        with patch.object(BirpayOrder, 'refresh_from_db', tracking_refresh):
            process_birpay_order(data)
        refresh_with_payment_id = [c for c in call_tracker if c.get('fields') == ['payment_id']]
        self.assertGreater(len(refresh_with_payment_id), 0, 'refresh_from_db(fields=["payment_id"]) должен быть вызван перед проверкой отправки на ASU')
        mock_send.assert_called_once()

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_order_with_payment_id_set_in_db_does_not_send_after_get_or_create(self, mock_send):
        """Заказ уже в БД с payment_id (например сохранён другим воркером) — get_or_create вернёт его, refresh подхватит payment_id, отправки не будет."""
        BirpayOrder.objects.create(
            birpay_id=5004,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-5004',
            merchant_user_id='user1',
            card_number='4111111111111111',
            status=0,
            status_internal=0,
            amount=100.0,
            operator='op',
            raw_data={},
            payment_id='already-in-db-from-other-worker',
        )
        data = _birpay_data(birpay_id=5004, merchant_tx_id='mtx-5004')
        process_birpay_order(data)
        mock_send.assert_not_called()
        order = BirpayOrder.objects.get(birpay_id=5004)
        self.assertEqual(order.payment_id, 'already-in-db-from-other-worker')

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_201_created_saves_payment_id(self, mock_send):
        """При ответе ASU 201 (создание) сохраняем payment_id и считаем заявку созданной."""
        mock_send.return_value = {'success': True, 'payment_id': 'uuid-201-created', 'created': True}
        data = _birpay_data(birpay_id=5005, merchant_tx_id='mtx-5005')
        process_birpay_order(data)
        order = BirpayOrder.objects.get(birpay_id=5005)
        self.assertEqual(order.payment_id, 'uuid-201-created')

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_200_idempotency_saves_payment_id(self, mock_send):
        """При ответе ASU 200 (идемпотентность — заявка уже есть) всё равно сохраняем payment_id, чтобы не слать повторно."""
        mock_send.return_value = {'success': True, 'payment_id': 'uuid-200-existing', 'created': False}
        data = _birpay_data(birpay_id=5006, merchant_tx_id='mtx-5006')
        process_birpay_order(data)
        order = BirpayOrder.objects.get(birpay_id=5006)
        self.assertEqual(order.payment_id, 'uuid-200-existing')


class TestZASUSendIdempotency(TestCase):
    """Проверка: send_birpay_order_to_z_asu возвращает created=True только при 201, при 200 — created=False."""

    def setUp(self):
        self.now = timezone.now()
        RequsiteZajon.objects.create(
            id=9002,
            active=True,
            agent_id=1,
            agent_name='Test Agent',
            name='Test Z-ASU requisite 2',
            weight=0,
            created_at=self.now,
            updated_at=self.now,
            card_number='4111111111111111',
            works_on_asu=True,
        )

    @patch('core.asu_pay_func._z_asu_manager')
    def test_send_birpay_order_returns_created_true_on_201(self, mock_manager):
        """ASU вернул 201 — заявка создана, Deposit получает created=True."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {'payment_id': 'pid-201-created'}
        mock_response.text = ''
        mock_response.reason = 'Created'
        mock_manager.make_request.return_value = mock_response

        from core.asu_pay_func import send_birpay_order_to_z_asu

        order = BirpayOrder.objects.create(
            birpay_id=5007,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-5007',
            merchant_user_id='user1',
            card_number='4111111111111111',
            status=0,
            status_internal=0,
            amount=100.0,
            operator='op',
            raw_data={},
        )
        result = send_birpay_order_to_z_asu(order)
        self.assertTrue(result['success'])
        self.assertEqual(result['payment_id'], 'pid-201-created')
        self.assertTrue(result['created'])

    @patch('core.asu_pay_func._z_asu_manager')
    def test_send_birpay_order_returns_created_false_on_200(self, mock_manager):
        """ASU вернул 200 (идемпотентность) — заявка уже была, Deposit получает created=False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'payment_id': 'pid-200-existing'}
        mock_response.text = ''
        mock_response.reason = 'OK'
        mock_manager.make_request.return_value = mock_response

        from core.asu_pay_func import send_birpay_order_to_z_asu

        order = BirpayOrder.objects.create(
            birpay_id=5008,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-5008',
            merchant_user_id='user1',
            card_number='4111111111111111',
            status=0,
            status_internal=0,
            amount=100.0,
            operator='op',
            raw_data={},
        )
        result = send_birpay_order_to_z_asu(order)
        self.assertTrue(result['success'])
        self.assertEqual(result['payment_id'], 'pid-200-existing')
        self.assertFalse(result['created'])
