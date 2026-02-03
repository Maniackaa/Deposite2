"""
Тесты логики Z-ASU: подтверждение/отклонение на ASU только при смене status модели BirpayOrder.
Задачи ставятся только при наличии payment_id (заявка создавалась на ASU).
При status=1 — задача подтверждения, при status=2 — задача отклонения.
"""
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from deposit.models import BirpayOrder, RequsiteZajon
from deposit.tasks import refresh_birpay_data


def _birpay_row(birpay_id, merchant_tx_id, status=0, card_number='4111111111111111', amount=100.0):
    """Данные одной заявки как возвращает get_birpays() для process_birpay_order."""
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
        'status': status,
        'payload': {},
        'paymentRequisite': {'payload': {'card_number': card_number}},
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

    @patch('deposit.tasks.get_birpays')
    @patch('deposit.tasks.confirm_z_asu_transaction_task')
    def test_refresh_birpay_data_status_1_signal_queues_confirm_task(
        self, mock_confirm_task, mock_get_birpays
    ):
        """
        refresh_birpay_data получает из Birpay заявку со status=1.
        process_birpay_order обновляет BirpayOrder (status 0 -> 1), сохраняет только изменённые поля.
        Сигнал birpay_order_z_asu_on_status_change ставит confirm_z_asu_transaction_task.
        """
        # Симулируем ответ get_birpays(): заявка подтверждена в Birpay (status=1)
        birpay_row = _birpay_row(
            birpay_id=7001,
            merchant_tx_id='mtx-refresh-7001',
            status=1,
            card_number='4111111111111111',
            amount=100.0,
        )
        mock_get_birpays.return_value = [birpay_row]

        refresh_birpay_data()

        mock_get_birpays.assert_called_once()
        mock_confirm_task.delay.assert_called_once_with(
            payment_id='uuid-asu-confirm-from-refresh',
            merchant_transaction_id=None,
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 1)
