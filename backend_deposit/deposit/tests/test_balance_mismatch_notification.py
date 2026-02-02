"""
Тесты для проверки отправки уведомлений при привязке BirpayOrder к Incoming с несовпадающим балансом.
"""
import pytest
from collections import OrderedDict
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
class TestBalanceMismatchNotification(TestCase):
    """Тесты отправки уведомлений при несовпадении баланса"""
    
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
        
        # Создаем BirpayOrder
        self.order = BirpayOrder.objects.create(
            birpay_id=12345,
            created_at=self.base_time - datetime.timedelta(hours=1),
            updated_at=self.base_time - datetime.timedelta(hours=1),
            merchant_transaction_id='100001',
            merchant_user_id='USER123',
            merchant_name='Test Merchant',
            customer_name='Test Customer',
            card_number='1234****5678',
            sender='Test Sender',
            status=0,
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
        self.incoming = Incoming.objects.create(
            register_date=sms_time,
            response_date=sms_time,
            recipient='1234****5678',
            sender='Bank',
            pay=100.0,
            balance=1100.1,  # НЕ совпадает с check_balance (1100.0) после округления
            transaction=999992,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        # Проверяем, что балансы действительно не совпадают
        self.incoming.refresh_from_db()
        self.assertIsNotNone(self.incoming.check_balance)
        self.assertEqual(self.incoming.check_balance, 1100.0)
        self.assertEqual(self.incoming.balance, 1100.1)

    def test_incomings_page_get(self):
        """Тест: получение страницы incomings через GET."""
        url = reverse('deposit:incomings')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, msg=f'GET {url} вернул {response.status_code}')
    
    @patch('deposit.views.send_message_tg')
    def test_notification_sent_on_balance_mismatch(self, mock_send_message_tg):
        """Тест: уведомление отправляется при привязке с несовпадающим балансом"""
        # Мокируем settings.ALARM_IDS
        with patch('deposit.views.settings.ALARM_IDS', ['123456789']):
            # Выполняем POST запрос для привязки
            # Формат: первый ключ - csrfmiddlewaretoken (автоматически), второй - поле с данными
            url = reverse('deposit:incomings')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                f'{self.incoming.id}-ok': self.order.merchant_transaction_id,
                f'confirm_balance_mismatch_{self.incoming.id}': '1'  # Подтверждаем привязку с несовпадающим балансом
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
            # Функция вызывается с keyword arguments: send_message_tg(message=msg, chat_ids=settings.ALARM_IDS)
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
            self.assertIn(str(self.incoming.id), message)
            self.assertIn(self.order.merchant_transaction_id, message)
            self.assertIn(str(self.incoming.balance), message)
            self.assertIn(str(self.incoming.check_balance), message)
            self.assertIn(self.incoming.recipient, message)
            self.assertIn(str(self.incoming.pay), message)
            self.assertIn(self.user.username, message)
            
            # Проверяем, что birpay_id был сохранен
            self.incoming.refresh_from_db()
            self.assertEqual(self.incoming.birpay_id, self.order.merchant_transaction_id)
    
    @patch('deposit.views.send_message_tg')
    def test_no_notification_on_balance_match(self, mock_send_message_tg):
        """Тест: уведомление НЕ отправляется при совпадающем балансе"""
        # Создаем Incoming с совпадающим балансом
        # Используем другой recipient, чтобы check_balance был вычислен правильно
        incoming_match = Incoming.objects.create(
            register_date=self.now_msk,
            response_date=self.now_msk,
            recipient='9999****9999',  # Другой получатель
            sender='Bank',
            pay=100.0,
            balance=100.0,  # Первая запись для этого получателя, check_balance будет None
            transaction=999993,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        # Для первой записи check_balance будет None, поэтому balance_mismatch будет False
        incoming_match.refresh_from_db()
        self.assertIsNone(incoming_match.check_balance)
        # View проверяет isinstance(incoming.birpay_id, NoneType) — явно сбрасываем
        incoming_match.birpay_id = None
        incoming_match.save(update_fields=['birpay_id'])

        with patch('deposit.views.settings.ALARM_IDS', ['123456789']):
            url = reverse('deposit:incomings')
            # GET в начале — страница может выставить CSRF-куку
            self.client.get(url)
            csrf_token = self.client.cookies.get('csrftoken')
            csrf_token = csrf_token.value if csrf_token else _get_csrf_token(self.client)
            # View берёт list(request.POST.keys())[1] — второй ключ должен быть "pk-ok"
            post_data = OrderedDict([
                ('csrfmiddlewaretoken', csrf_token),
                (f'{incoming_match.id}-ok', self.order.merchant_transaction_id),
            ])
            response = self.client.post(url, post_data, HTTP_X_CSRFTOKEN=csrf_token)
            
            # При успехе view делает redirect (302), не follow — избегаем ошибок debug_toolbar при GET
            self.assertEqual(response.status_code, 302, msg=f'Ожидали redirect, получили {response.status_code}: {getattr(response, "content", b"")[:200]}')
            
            # Проверяем, что send_message_tg НЕ был вызван
            self.assertFalse(mock_send_message_tg.called, "send_message_tg НЕ должен быть вызван при совпадающем балансе")
    
    @patch('deposit.views.send_message_tg')
    def test_notification_contains_correct_data(self, mock_send_message_tg):
        """Тест: уведомление содержит правильные данные"""
        with patch('deposit.views.settings.ALARM_IDS', ['123456789', '987654321']):
            url = reverse('deposit:incomings')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                f'{self.incoming.id}-ok': self.order.merchant_transaction_id,
                f'confirm_balance_mismatch_{self.incoming.id}': '1'  # Подтверждаем привязку с несовпадающим балансом
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
                str(self.incoming.id),
                self.order.merchant_transaction_id,
                str(self.incoming.balance),
                str(self.incoming.check_balance),
                self.incoming.recipient,
                str(self.incoming.pay),
                self.user.username
            ]

            for part in expected_parts:
                self.assertIn(part, message, f"Сообщение должно содержать: {part}")
            
            # Проверяем chat_ids
            self.assertEqual(chat_ids, ['123456789', '987654321'])
    
    @patch('deposit.views.send_message_tg')
    def test_notification_error_handling(self, mock_send_message_tg):
        """Тест: ошибка при отправке уведомления не прерывает выполнение"""
        # Мокируем send_message_tg чтобы он выбрасывал исключение
        mock_send_message_tg.side_effect = Exception("Telegram API error")
        
        with patch('deposit.views.settings.ALARM_IDS', ['123456789']):
            url = reverse('deposit:incomings')
            csrf_token = _get_csrf_token(self.client)
            response = self.client.post(url, {
                'csrfmiddlewaretoken': csrf_token,
                f'{self.incoming.id}-ok': self.order.merchant_transaction_id,
                f'confirm_balance_mismatch_{self.incoming.id}': '1'  # Подтверждаем привязку с несовпадающим балансом
            }, follow=True, HTTP_X_CSRFTOKEN=csrf_token)
            
            # Проверяем, что запрос выполнен успешно
            self.assertEqual(response.status_code, 200)
            
            # Проверяем, что send_message_tg был вызван
            self.assertTrue(mock_send_message_tg.called)
            
            # Проверяем, что birpay_id был сохранен несмотря на ошибку
            self.incoming.refresh_from_db()
            self.assertEqual(self.incoming.birpay_id, self.order.merchant_transaction_id)

