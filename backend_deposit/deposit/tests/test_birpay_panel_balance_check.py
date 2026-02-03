"""
Тесты для проверки проверки баланса при смене статуса на approve в birpay_panel.
"""
import pytest
from unittest.mock import patch, Mock
from django.test import TestCase, Client, RequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
from django.middleware.csrf import get_token
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
import datetime
import pytz

from deposit.models import BirpayOrder, Incoming
from core.global_func import TZ


def _get_csrf_token(client):
    """Получить CSRF-токен для тестового клиента (не зависит от куки в ответе GET)."""
    request = RequestFactory().get('/')
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.session.save()
    token = get_token(request)
    client.cookies['csrftoken'] = token
    return token


@pytest.mark.django_db
class TestBirpayPanelBalanceCheck(TestCase):
    """Тесты проверки баланса при смене статуса на approve в birpay_panel"""
    
    def setUp(self):
        """Подготовка тестовых данных"""
        User = get_user_model()
        self.user = User.objects.create_user(
            username='testuser',
            email='test@test.com',
            password='testpass123',
            is_staff=True
        )
        
        self.client = Client()
        self.client.force_login(self.user)
        
        self.base_time = timezone.now()
        self.now_msk = self.base_time.astimezone(TZ)
        
        # Создаем BirpayOrder со статусом 0 (pending)
        self.order = BirpayOrder.objects.create(
            birpay_id=12345,
            created_at=self.base_time - datetime.timedelta(hours=1),
            updated_at=self.base_time - datetime.timedelta(hours=1),
            merchant_transaction_id='100001',
            merchant_user_id='TEST_DEV_USER_1',
            merchant_name='Test Merchant',
            customer_name='Test Customer',
            card_number='1234****5678',
            sender='Test Sender',
            status=0,  # pending
            status_internal=0,
            amount=100.0,
            operator='test_operator',
            raw_data={'test': 'data'},
        )
        
        # Создаем предыдущую SMS для расчета баланса
        prev_sms_time = self.now_msk - datetime.timedelta(minutes=10)
        self.prev_incoming = Incoming.objects.create(
            register_date=prev_sms_time,
            response_date=prev_sms_time,
            recipient='1234****5678',
            sender='Bank',
            pay=50.0,
            balance=1000.0,  # Предыдущий баланс
            transaction=999991,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        # Создаем текущую SMS с НЕСОВПАДАЮЩИМ балансом
        # check_balance = 1000.0 + 100.0 = 1100.0
        # balance = 1100.1 (не совпадает после округления)
        sms_time = self.now_msk
        self.incoming_mismatch = Incoming.objects.create(
            register_date=sms_time,
            response_date=sms_time,
            recipient='1234****5678',
            sender='Bank',
            pay=100.0,  # Совпадает с order.amount
            balance=1100.1,  # НЕ совпадает с check_balance (1100.0) после округления
            transaction=999992,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        # Создаем текущую SMS с СОВПАДАЮЩИМ балансом
        sms_time2 = self.now_msk + datetime.timedelta(minutes=1)
        self.incoming_match = Incoming.objects.create(
            register_date=sms_time2,
            response_date=sms_time2,
            recipient='9999****9999',  # Другой получатель
            sender='Bank',
            pay=100.0,  # Совпадает с order.amount
            balance=100.0,  # Первая запись для этого получателя, check_balance будет None
            transaction=999993,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        # Проверяем, что балансы действительно не совпадают для incoming_mismatch
        self.incoming_mismatch.refresh_from_db()
        self.assertIsNotNone(self.incoming_mismatch.check_balance)
        self.assertEqual(self.incoming_mismatch.check_balance, 1100.0)
        self.assertEqual(self.incoming_mismatch.balance, 1100.1)
    
    @patch('core.birpay_func.change_amount_birpay')
    @patch('core.birpay_func.approve_birpay_refill')
    @patch('deposit.views.send_message_tg')
    def test_notification_sent_on_balance_mismatch_approve(self, mock_send_message_tg, mock_approve_birpay_refill, mock_change_amount_birpay):
        """Тест: уведомление отправляется при approve с несовпадающим балансом"""
        # Мокируем approve_birpay_refill для успешного ответа
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = 'OK'
        mock_approve_birpay_refill.return_value = mock_response
        
        # Мокируем change_amount_birpay чтобы не пытаться изменить сумму
        mock_change_response = Mock()
        mock_change_response.status_code = 200
        mock_change_response.text = 'OK'
        mock_change_amount_birpay.return_value = mock_change_response
        
        with patch('deposit.views.settings.ALARM_IDS', ['123456789']):
            url = reverse('deposit:birpay_panel')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                'orderconfirm_{}'.format(self.order.id): str(self.incoming_mismatch.id),
                'order_action_{}'.format(self.order.id): 'approve',
                'orderamount_{}'.format(self.order.id): str(self.order.amount),  # Передаем текущую сумму, чтобы не менялась
                'confirm_balance_mismatch_{}'.format(self.order.id): '1',  # Подтверждаем привязку с несовпадающим балансом
            }, follow=True, HTTP_X_CSRFTOKEN=csrf_token)
            
            # Проверяем, что запрос выполнен успешно
            self.assertEqual(response.status_code, 200)
            
            # Проверяем, что send_message_tg был вызван
            self.assertTrue(mock_send_message_tg.called, 
                          f"send_message_tg должен быть вызван. Вызовов: {mock_send_message_tg.call_count}")
            
            # Проверяем количество вызовов
            self.assertEqual(mock_send_message_tg.call_count, 1, 
                           f"send_message_tg должен быть вызван один раз, но был вызван {mock_send_message_tg.call_count} раз")
            
            # Получаем аргументы вызова
            call_args = mock_send_message_tg.call_args
            self.assertIsNotNone(call_args, "send_message_tg должен быть вызван с аргументами")
            
            # Проверяем аргументы
            message = call_args.kwargs.get('message') if call_args.kwargs else None
            chat_ids = call_args.kwargs.get('chat_ids') if call_args.kwargs else None
            
            # Если нет kwargs, проверяем args
            if message is None and call_args.args:
                message = call_args.args[0] if len(call_args.args) > 0 else None
            if chat_ids is None and call_args.args and len(call_args.args) > 1:
                chat_ids = call_args.args[1]
            
            self.assertIsNotNone(message, "Сообщение должно быть передано")
            self.assertIsNotNone(chat_ids, "chat_ids должны быть переданы")
            
            # Проверяем содержимое сообщения
            self.assertIn('ВНИМАНИЕ', message)
            self.assertIn('несовпадающим балансом', message)
            self.assertIn(str(self.incoming_mismatch.id), message)
            self.assertIn(str(self.order.id), message)
            self.assertIn(self.order.merchant_transaction_id, message)
            self.assertIn(str(self.incoming_mismatch.balance), message)
            self.assertIn(str(self.incoming_mismatch.check_balance), message)
            self.assertIn(self.incoming_mismatch.recipient, message)
            self.assertIn(str(self.incoming_mismatch.pay), message)
            self.assertIn(self.user.username, message)
    
    @patch('core.birpay_func.change_amount_birpay')
    @patch('core.birpay_func.approve_birpay_refill')
    @patch('deposit.views.send_message_tg')
    def test_no_notification_on_balance_match_approve(self, mock_send_message_tg, mock_approve_birpay_refill, mock_change_amount_birpay):
        """Тест: уведомление НЕ отправляется при approve с совпадающим балансом"""
        # Мокируем approve_birpay_refill для успешного ответа
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = 'OK'
        mock_approve_birpay_refill.return_value = mock_response
        
        # Мокируем change_amount_birpay чтобы не пытаться изменить сумму
        mock_change_response = Mock()
        mock_change_response.status_code = 200
        mock_change_response.text = 'OK'
        mock_change_amount_birpay.return_value = mock_change_response
        
        # Для первой записи check_balance будет None, поэтому balance_mismatch будет False
        self.incoming_match.refresh_from_db()
        self.assertIsNone(self.incoming_match.check_balance)
        
        with patch('deposit.views.settings.ALARM_IDS', ['123456789']):
            url = reverse('deposit:birpay_panel')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                'orderconfirm_{}'.format(self.order.id): str(self.incoming_match.id),
                'order_action_{}'.format(self.order.id): 'approve',
                'orderamount_{}'.format(self.order.id): str(self.order.amount),  # Передаем текущую сумму, чтобы не менялась
                'confirm_balance_mismatch_{}'.format(self.order.id): '0',  # Баланс совпадает, флаг не нужен
            }, follow=True, HTTP_X_CSRFTOKEN=csrf_token)
            
            # Проверяем, что запрос выполнен успешно
            self.assertEqual(response.status_code, 200)
            
            # Проверяем, что send_message_tg НЕ был вызван
            self.assertFalse(mock_send_message_tg.called, 
                          "send_message_tg НЕ должен быть вызван при совпадающем балансе")
    
    @patch('core.birpay_func.change_amount_birpay')
    @patch('core.birpay_func.approve_birpay_refill')
    @patch('deposit.views.send_message_tg')
    def test_notification_contains_correct_data_approve(self, mock_send_message_tg, mock_approve_birpay_refill, mock_change_amount_birpay):
        """Тест: уведомление содержит правильные данные при approve"""
        # Мокируем approve_birpay_refill для успешного ответа
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = 'OK'
        mock_approve_birpay_refill.return_value = mock_response
        
        # Мокируем change_amount_birpay чтобы не пытаться изменить сумму
        mock_change_response = Mock()
        mock_change_response.status_code = 200
        mock_change_response.text = 'OK'
        mock_change_amount_birpay.return_value = mock_change_response
        
        with patch('deposit.views.settings.ALARM_IDS', ['123456789', '987654321']):
            url = reverse('deposit:birpay_panel')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                'orderconfirm_{}'.format(self.order.id): str(self.incoming_mismatch.id),
                'order_action_{}'.format(self.order.id): 'approve',
                'orderamount_{}'.format(self.order.id): str(self.order.amount),  # Передаем текущую сумму, чтобы не менялась
                'confirm_balance_mismatch_{}'.format(self.order.id): '1',  # Подтверждаем привязку с несовпадающим балансом
            }, follow=True, HTTP_X_CSRFTOKEN=csrf_token)
            
            self.assertEqual(response.status_code, 200)
            self.assertTrue(mock_send_message_tg.called)
            
            # Получаем аргументы вызова
            call_args = mock_send_message_tg.call_args
            self.assertIsNotNone(call_args, "send_message_tg должен быть вызван с аргументами")
            
            # Функция вызывается с keyword arguments
            message = call_args.kwargs.get('message') if call_args.kwargs else None
            chat_ids = call_args.kwargs.get('chat_ids') if call_args.kwargs else None
            
            # Если нет kwargs, проверяем args
            if message is None and call_args.args:
                message = call_args.args[0] if len(call_args.args) > 0 else None
            if chat_ids is None and call_args.args and len(call_args.args) > 1:
                chat_ids = call_args.args[1]
            
            self.assertIsNotNone(message, "Сообщение должно быть передано")
            self.assertIsNotNone(chat_ids, "chat_ids должны быть переданы")

            # Проверяем формат сообщения
            expected_parts = [
                'ВНИМАНИЕ',
                'несовпадающим балансом',
                str(self.incoming_mismatch.id),
                str(self.order.id),
                self.order.merchant_transaction_id,
                str(self.incoming_mismatch.balance),
                str(self.incoming_mismatch.check_balance),
                self.incoming_mismatch.recipient,
                str(self.incoming_mismatch.pay),
                self.user.username
            ]

            for part in expected_parts:
                self.assertIn(part, message, f"Сообщение должно содержать: {part}")
            
            # Проверяем chat_ids
            self.assertEqual(chat_ids, ['123456789', '987654321'])
    
    @patch('core.birpay_func.change_amount_birpay')
    @patch('core.birpay_func.approve_birpay_refill')
    @patch('deposit.views.send_message_tg')
    def test_notification_error_handling_approve(self, mock_send_message_tg, mock_approve_birpay_refill, mock_change_amount_birpay):
        """Тест: ошибка при отправке уведомления не прерывает выполнение при approve"""
        # Мокируем approve_birpay_refill для успешного ответа
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = 'OK'
        mock_approve_birpay_refill.return_value = mock_response
        
        # Мокируем change_amount_birpay чтобы не пытаться изменить сумму
        mock_change_response = Mock()
        mock_change_response.status_code = 200
        mock_change_response.text = 'OK'
        mock_change_amount_birpay.return_value = mock_change_response
        
        # Мокируем send_message_tg чтобы он выбрасывал исключение
        mock_send_message_tg.side_effect = Exception("Telegram API error")
        
        with patch('deposit.views.settings.ALARM_IDS', ['123456789']):
            url = reverse('deposit:birpay_panel')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                'orderconfirm_{}'.format(self.order.id): str(self.incoming_mismatch.id),
                'order_action_{}'.format(self.order.id): 'approve',
                'orderamount_{}'.format(self.order.id): str(self.order.amount),  # Передаем текущую сумму, чтобы не менялась
                'confirm_balance_mismatch_{}'.format(self.order.id): '1',  # Подтверждаем привязку с несовпадающим балансом
            }, follow=True, HTTP_X_CSRFTOKEN=csrf_token)
            
            # Проверяем, что запрос выполнен успешно
            self.assertEqual(response.status_code, 200)
            
            # Проверяем, что send_message_tg был вызван
            self.assertTrue(mock_send_message_tg.called)

