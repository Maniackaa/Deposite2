"""
Тесты логики Z-ASU: подтверждение/отклонение на ASU только при смене status модели BirpayOrder.
Задачи ставятся только при наличии payment_id (заявка создавалась на ASU).
При status=1 — задача подтверждения, при status=2 — задача отклонения.
"""
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from deposit.models import BirpayOrder, RequsiteZajon
from deposit.tasks import process_birpay_order, refresh_birpay_data, send_birpay_order_to_z_asu_task


def _birpay_row(birpay_id, merchant_tx_id, status=0, card_number='4111111111111111', amount=100.0, requisite_id=None):
    """Данные одной заявки как возвращает get_birpays() для process_birpay_order."""
    now = timezone.now()
    payment_requisite = {'payload': {'card_number': card_number}}
    if requisite_id is not None:
        payment_requisite['id'] = requisite_id
    return {
        'id': birpay_id,
        'merchantTransactionId': merchant_tx_id,
        'createdAt': now.isoformat(),
        'updatedAt': now.isoformat(),
        'merchantUserId': 'user1',
        'merchant': {'name': 'Test'},
        'customerName': 'Customer',
        'amount': str(amount),
        'status': status,
        'payload': {},
        'paymentRequisite': payment_requisite,
    }


class TestZASUStatusTasks(TestCase):
    """Проверка постановки задач ASU при смене status заявки (только при payment_id)."""

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
        # Заявка с payment_id — по ней ставятся задачи ASU при смене status
        self.order_with_payment_id = BirpayOrder.objects.create(
            birpay_id=5001,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-z-asu-confirm-test',
            merchant_user_id='user1',
            card_number='4111111111111111',
            status=0,
            status_internal=0,
            amount=100.0,
            operator='op',
            raw_data={},
            payment_id='a1b2c3d4-e5f6-7890-abcd-ef1234567890',
        )
        # Заявка без payment_id — задачи ASU не ставятся
        self.order_no_payment_id = BirpayOrder.objects.create(
            birpay_id=5002,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-no-z-asu',
            merchant_user_id='user2',
            card_number='9999999999999999',
            status=0,
            status_internal=0,
            amount=50.0,
            operator='op',
            raw_data={},
        )

    @patch('deposit.tasks.confirm_z_asu_transaction_task')
    def test_status_change_to_1_queues_confirm_task(self, mock_confirm_task):
        """При смене status на 1 и наличии payment_id ставится задача подтверждения на ASU."""
        self.order_with_payment_id.status = 1
        self.order_with_payment_id.save(update_fields=['status'])
        mock_confirm_task.delay.assert_called_once_with(
            payment_id='a1b2c3d4-e5f6-7890-abcd-ef1234567890',
            merchant_transaction_id=None,
        )

    @patch('deposit.tasks.confirm_z_asu_transaction_task')
    def test_status_change_to_1_with_payment_id_passes_payment_id(self, mock_confirm_task):
        """При смене status на 1 задача вызывается с payment_id (merchant_transaction_id=None)."""
        self.order_with_payment_id.status = 1
        self.order_with_payment_id.save(update_fields=['status'])
        mock_confirm_task.delay.assert_called_once_with(
            payment_id='a1b2c3d4-e5f6-7890-abcd-ef1234567890',
            merchant_transaction_id=None,
        )

    @patch('deposit.tasks.decline_z_asu_transaction_task')
    def test_status_change_to_2_queues_decline_task(self, mock_decline_task):
        """При смене status на 2 и наличии payment_id ставится задача отклонения на ASU."""
        self.order_with_payment_id.status = 2
        self.order_with_payment_id.save(update_fields=['status'])
        mock_decline_task.delay.assert_called_once_with(
            payment_id='a1b2c3d4-e5f6-7890-abcd-ef1234567890',
            merchant_transaction_id=None,
        )

    @patch('deposit.tasks.decline_z_asu_transaction_task')
    def test_status_change_to_2_with_payment_id_passes_payment_id(self, mock_decline_task):
        """При смене status на 2 задача вызывается с payment_id."""
        self.order_with_payment_id.status = 2
        self.order_with_payment_id.save(update_fields=['status'])
        mock_decline_task.delay.assert_called_once_with(
            payment_id='a1b2c3d4-e5f6-7890-abcd-ef1234567890',
            merchant_transaction_id=None,
        )

    @patch('deposit.tasks.confirm_z_asu_transaction_task')
    def test_status_change_to_1_without_payment_id_does_not_queue_task(self, mock_confirm_task):
        """Без payment_id задача подтверждения не ставится."""
        self.order_no_payment_id.status = 1
        self.order_no_payment_id.save(update_fields=['status'])
        mock_confirm_task.delay.assert_not_called()

    @patch('deposit.tasks.decline_z_asu_transaction_task')
    def test_status_change_to_2_without_payment_id_does_not_queue_task(self, mock_decline_task):
        """Без payment_id задача отклонения не ставится."""
        self.order_no_payment_id.status = 2
        self.order_no_payment_id.save(update_fields=['status'])
        mock_decline_task.delay.assert_not_called()

    @patch('deposit.tasks.confirm_z_asu_transaction_task')
    def test_status_unchanged_does_not_queue_confirm_task(self, mock_confirm_task):
        """Если status не менялся (остался 1), повторная запись не ставит задачу."""
        self.order_with_payment_id.status = 1
        self.order_with_payment_id.save(update_fields=['status'])
        self.assertEqual(mock_confirm_task.delay.call_count, 1)
        mock_confirm_task.delay.reset_mock()
        self.order_with_payment_id.amount = 200.0
        self.order_with_payment_id.save(update_fields=['amount', 'status'])
        mock_confirm_task.delay.assert_not_called()


class TestRefreshBirpayDataAsuConfirm(TestCase):
    """
    Полный путь refresh_birpay_data: при обновлении заявки со статусом 1 из Birpay
    сигнал отрабатывает и ставит задачу подтверждения на ASU.
    """

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
        # Заявка уже на ASU (есть payment_id), в Birpay ещё status=0
        self.order = BirpayOrder.objects.create(
            birpay_id=7001,
            sended_at=self.now,
            created_at=self.now,
            updated_at=self.now,
            merchant_transaction_id='mtx-refresh-7001',
            merchant_user_id='user1',
            card_number='4111111111111111',
            status=0,
            status_internal=0,
            amount=100.0,
            operator='op',
            raw_data={},
            payment_id='uuid-asu-confirm-from-refresh',
        )

    @patch('deposit.tasks.BirpayClient')
    @patch('deposit.tasks.confirm_z_asu_transaction_task')
    def test_refresh_birpay_data_status_1_signal_queues_confirm_task(
        self, mock_confirm_task, mock_birpay_client_cls
    ):
        """
        refresh_birpay_data получает из Birpay заявку со status=1.
        process_birpay_order обновляет BirpayOrder (status 0 -> 1), сохраняет только изменённые поля.
        Сигнал birpay_order_z_asu_on_status_change ставит confirm_z_asu_transaction_task.
        """
        # Симулируем ответ get_refill_orders(): заявка подтверждена в Birpay (status=1)
        birpay_row = _birpay_row(
            birpay_id=7001,
            merchant_tx_id='mtx-refresh-7001',
            status=1,
            card_number='4111111111111111',
            amount=100.0,
        )
        mock_birpay_client_cls.return_value.get_refill_orders.return_value = [birpay_row]

        refresh_birpay_data()

        mock_birpay_client_cls.return_value.get_refill_orders.assert_called_once()
        mock_confirm_task.delay.assert_called_once_with(
            payment_id='uuid-asu-confirm-from-refresh',
            merchant_transaction_id=None,
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 1)


class TestProcessBirpayOrderSendsToZAsu(TestCase):
    """При создании заявки с реквизитом works_on_asu=True делается попытка создания на ASU."""

    def setUp(self):
        self._original_eager = send_birpay_order_to_z_asu_task.app.conf.task_always_eager
        send_birpay_order_to_z_asu_task.app.conf.task_always_eager = True
        self.addCleanup(self._restore_eager)
        self.now = timezone.now()
        self.requisite_id = 9010
        RequsiteZajon.objects.create(
            id=self.requisite_id,
            active=True,
            agent_id=1,
            agent_name='Test Agent',
            name='Test Z-ASU requisite for create',
            weight=0,
            created_at=self.now,
            updated_at=self.now,
            card_number='4189800086502240',
            works_on_asu=True,
        )

    def _restore_eager(self):
        send_birpay_order_to_z_asu_task.app.conf.task_always_eager = self._original_eager

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_create_order_with_works_on_asu_requisite_calls_send_to_z_asu(self, mock_send):
        """При создании заявки с привязанным реквизитом works_on_asu=True вызывается send_birpay_order_to_z_asu."""
        mock_send.return_value = {'success': True, 'payment_id': 'test-payment-uuid', 'created': True}
        data = _birpay_row(
            birpay_id=8001,
            merchant_tx_id='mtx-create-z-asu-8001',
            status=0,
            card_number='4189800086502240',
            amount=20.0,
            requisite_id=self.requisite_id,
        )
        order, created, updated = process_birpay_order(data)

        self.assertTrue(created)
        self.assertIsNotNone(order.requisite_id)
        self.assertEqual(order.requisite_id, self.requisite_id)
        mock_send.assert_called_once()
        call_order = mock_send.call_args[0][0]
        self.assertEqual(call_order.birpay_id, 8001)
        self.assertEqual(call_order.requisite_id, self.requisite_id)

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_create_order_with_requisite_works_on_asu_false_does_not_call_send(self, mock_send):
        """При создании заявки с реквизитом works_on_asu=False отправка на ASU не вызывается."""
        RequsiteZajon.objects.filter(pk=self.requisite_id).update(works_on_asu=False)
        data = _birpay_row(
            birpay_id=8002,
            merchant_tx_id='mtx-no-z-asu-8002',
            status=0,
            requisite_id=self.requisite_id,
        )
        order, created, updated = process_birpay_order(data)

        self.assertTrue(created)
        self.assertEqual(order.requisite_id, self.requisite_id)
        mock_send.assert_not_called()

    @patch('deposit.tasks.send_birpay_order_to_z_asu')
    def test_create_order_without_requisite_does_not_call_send(self, mock_send):
        """При создании заявки без привязки реквизита отправка на ASU не вызывается."""
        data = _birpay_row(
            birpay_id=8003,
            merchant_tx_id='mtx-no-requisite-8003',
            status=0,
            requisite_id=None,  # нет id в paymentRequisite
        )
        order, created, updated = process_birpay_order(data)

        self.assertTrue(created)
        self.assertIsNone(order.requisite_id)
        mock_send.assert_not_called()
