"""
Простой тест для проверки привязки SMS к заказу
"""
import pytest
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch, Mock

from deposit.models import BirpayOrder, Incoming

User = get_user_model()


@pytest.mark.django_db
class SMSBindingTest(TestCase):
    """Тест привязки SMS к заказу"""
    
    def setUp(self):
        """Создаем тестовые данные"""
        # Создаем пользователя
        self.user = User.objects.create_user(
            username='testuser',
            email='test@test.com',
            password='testpass123',
            is_staff=True
        )
        
        # Профиль создается автоматически через сигнал
        self.user.profile.assigned_card_numbers = ['1234567890123456']
        self.user.profile.save()
        
        # Создаем заказ
        self.birpay_order = BirpayOrder.objects.create(
            birpay_id=12345,
            created_at='2025-01-01 10:00:00+00:00',
            updated_at='2025-01-01 10:00:00+00:00',
            merchant_transaction_id='MTX123456',
            merchant_user_id='USER123',
            merchant_name='Test Merchant',
            customer_name='Test Customer',
            card_number='1234567890123456',
            sender='Test Sender',
            status=0,  # pending
            status_internal=0,  # pending
            amount=100.0,
            operator='test_operator',
            raw_data={'test': 'data'}
        )
        
        # Создаем SMS
        self.incoming = Incoming.objects.create(
            register_date='2025-01-01 10:00:00+00:00',
            response_date='2025-01-01 10:00:00+00:00',
            recipient='Test Recipient',
            sender='Test Sender',
            pay=100.0,  # Сумма как в заказе
            balance=500.0,
            transaction=123456789,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        self.client = Client()
        self.client.force_login(self.user)
    
    @patch('deposit.views.approve_birpay_refill')
    def test_sms_binding_success(self, mock_approve):
        """Тест успешной привязки SMS к заказу"""
        # Мокаем успешный ответ от API
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true}'
        mock_approve.return_value = mock_response
        
        # Данные для POST запроса (как в реальной форме)
        post_data = {
            'orderamount_{}'.format(self.birpay_order.id): '100.0',  # Сумма заказа
            'orderconfirm_{}'.format(self.birpay_order.id): str(self.incoming.id),  # SMS ID
            'order_action_approve': 'approve'  # Действие
        }
        
        # Выполняем POST запрос
        response = self.client.post(
            reverse('deposit:birpay_panel'),
            data=post_data,
            follow=True
        )
        
        # Проверяем успешный редирект
        self.assertEqual(response.status_code, 200)
        
        # Проверяем, что API был вызван
        mock_approve.assert_called_once_with(pk=self.birpay_order.birpay_id)
        
        # Проверяем изменения в базе данных
        self.birpay_order.refresh_from_db()
        self.incoming.refresh_from_db()
        
        # SMS должен быть привязан к заказу
        self.assertEqual(self.birpay_order.incoming, self.incoming)
        self.assertEqual(self.birpay_order.incomingsms_id, str(self.incoming.id))
        self.assertEqual(self.birpay_order.confirmed_operator, self.user)
        self.assertIsNotNone(self.birpay_order.confirmed_time)
        
        # Заказ должен быть привязан к SMS
        self.assertEqual(self.incoming.birpay_id, self.birpay_order.merchant_transaction_id)
        
        # Статусы должны быть обновлены
        self.assertEqual(self.birpay_order.status, 1)  # approved
        self.assertEqual(self.birpay_order.status_internal, 1)  # approved
    
    @patch('deposit.views.approve_birpay_refill')
    def test_birpay_order_display_in_table(self, mock_approve):
        """Тест отображения заказа в таблице birpay_orders после привязки SMS"""
        # Мокаем успешный ответ от API
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true}'
        mock_approve.return_value = mock_response
        
        # Данные для POST запроса
        post_data = {
            'orderamount_{}'.format(self.birpay_order.id): '100.0',
            'orderconfirm_{}'.format(self.birpay_order.id): str(self.incoming.id),
            'order_action_approve': 'approve'
        }
        
        # Выполняем POST запрос для привязки SMS
        response = self.client.post(
            reverse('deposit:birpay_panel'),
            data=post_data,
            follow=True
        )
        
        # Проверяем успешный редирект
        self.assertEqual(response.status_code, 200)
        
        # Теперь проверяем отображение в таблице
        response = self.client.get(reverse('deposit:birpay_panel'))
        self.assertEqual(response.status_code, 200)
        
        # Проверяем, что заказ отображается в таблице
        self.assertContains(response, self.birpay_order.merchant_transaction_id)  # Tx ID
        self.assertContains(response, self.birpay_order.merchant_user_id)  # UserID
        self.assertContains(response, str(self.birpay_order.amount))  # Сумма
        
        # Проверяем, что статус отображается как approve (1)
        self.assertContains(response, 'approve')
        
        # Проверяем, что привязанный SMS отображается в столбце
        self.assertContains(response, str(self.incoming.id))
        
        # Проверяем, что заказ имеет статус 1 в базе данных
        self.birpay_order.refresh_from_db()
        self.assertEqual(self.birpay_order.status, 1)
        
        # Проверяем, что SMS привязан
        self.assertEqual(self.birpay_order.incoming, self.incoming)
        self.assertEqual(self.birpay_order.incomingsms_id, str(self.incoming.id))
    
    @patch('deposit.views.approve_birpay_refill')
    def test_incoming_binding_via_merch_tx_id(self, mock_approve):
        """Тест привязки Incoming к BirpayOrder через MerchTxID в форме incomings"""
        # Мокаем успешный ответ от API
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true}'
        mock_approve.return_value = mock_response
        
        # Создаем отдельный Incoming
        separate_incoming = Incoming.objects.create(
            register_date='2025-01-01 11:00:00+00:00',
            response_date='2025-01-01 11:00:00+00:00',
            recipient='Separate Recipient',
            sender='Separate Sender',
            pay=150.0,  # Другая сумма
            balance=600.0,
            transaction=987654321,
            type='sms',
            worker='manual',
            birpay_id=None  # Пока не привязан
        )
        
        # Создаем отдельный BirpayOrder
        separate_order = BirpayOrder.objects.create(
            birpay_id=54321,
            created_at='2025-01-01 11:00:00+00:00',
            updated_at='2025-01-01 11:00:00+00:00',
            merchant_transaction_id='MTX789012',
            merchant_user_id='USER456',
            merchant_name='Separate Merchant',
            customer_name='Separate Customer',
            card_number='9876543210987654',
            sender='Separate Sender',
            status=0,  # pending
            status_internal=0,  # pending
            amount=150.0,  # Такая же сумма как в incoming
            operator='separate_operator',
            raw_data={'test': 'separate_data'}
        )
        
        # Данные для POST запроса (как в форме incomings)
        post_data = {
            'orderamount_{}'.format(separate_order.id): '150.0',  # Сумма заказа
            'orderconfirm_{}'.format(separate_order.id): str(separate_incoming.id),  # SMS ID
            'order_action_approve': 'approve'  # Действие
        }
        
        # Выполняем POST запрос для привязки
        response = self.client.post(
            reverse('deposit:birpay_panel'),
            data=post_data,
            follow=True
        )
        
        # Проверяем успешный редирект
        self.assertEqual(response.status_code, 200)
        
        # Проверяем, что API был вызван
        mock_approve.assert_called_once_with(pk=separate_order.birpay_id)
        
        # Проверяем изменения в базе данных
        separate_order.refresh_from_db()
        separate_incoming.refresh_from_db()
        
        # SMS должен быть привязан к заказу
        self.assertEqual(separate_order.incoming, separate_incoming)
        self.assertEqual(separate_order.incomingsms_id, str(separate_incoming.id))
        self.assertEqual(separate_order.confirmed_operator, self.user)
        self.assertIsNotNone(separate_order.confirmed_time)
        
        # Заказ должен быть привязан к SMS
        self.assertEqual(separate_incoming.birpay_id, separate_order.merchant_transaction_id)
        
        # Статусы должны быть обновлены
        self.assertEqual(separate_order.status, 1)  # approved
        self.assertEqual(separate_order.status_internal, 1)  # approved
        
        # Теперь проверяем отображение в таблице
        response = self.client.get(reverse('deposit:birpay_panel'))
        self.assertEqual(response.status_code, 200)
        
        # Проверяем, что отдельный заказ отображается в таблице
        self.assertContains(response, separate_order.merchant_transaction_id)  # MTX789012
        self.assertContains(response, separate_order.merchant_user_id)  # USER456
        self.assertContains(response, str(separate_order.amount))  # 150.0
        
        # Проверяем, что статус отображается как approve
        self.assertContains(response, 'approve')
        
        # Проверяем, что привязанный SMS отображается в столбце
        self.assertContains(response, str(separate_incoming.id))
