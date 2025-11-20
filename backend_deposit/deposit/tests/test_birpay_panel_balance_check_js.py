"""
Тесты для проверки JavaScript логики проверки баланса на странице birpay_panel.
"""
import pytest
from unittest.mock import patch, Mock
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
import datetime
import json

from deposit.models import BirpayOrder, Incoming
from core.global_func import TZ


@pytest.mark.django_db
class TestBirpayPanelBalanceCheckJS(TestCase):
    """Тесты JavaScript логики проверки баланса на birpay_panel"""
    
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
        
        # Проверяем, что балансы действительно не совпадают
        self.incoming_mismatch.refresh_from_db()
        self.assertIsNotNone(self.incoming_mismatch.check_balance)
        self.assertEqual(self.incoming_mismatch.check_balance, 1100.0)
        self.assertEqual(self.incoming_mismatch.balance, 1100.1)
    
    def test_birpay_panel_page_loads(self):
        """Тест: страница birpay_panel загружается"""
        url = reverse('deposit:birpay_panel')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'birpay_panel')
    
    def test_birpay_panel_contains_order(self):
        """Тест: страница содержит наш заказ"""
        url = reverse('deposit:birpay_panel')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.order.merchant_transaction_id))
    
    def test_birpay_panel_contains_balance_check_script(self):
        """Тест: страница содержит JavaScript для проверки баланса"""
        url = reverse('deposit:birpay_panel')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Проверяем наличие ключевых элементов JavaScript
        self.assertContains(response, 'balance_mismatch')
        self.assertContains(response, 'incoming-id-input')
        self.assertContains(response, 'confirm-balance-mismatch')
        # Проверяем правильный URL для API запроса (без префикса /deposit/)
        self.assertContains(response, '/api/incoming_balance_info/')
        # Проверяем, что старый URL не используется
        self.assertNotContains(response, '/deposit/api/incoming_balance_info/')
    
    def test_birpay_panel_contains_form_elements(self):
        """Тест: форма содержит необходимые элементы"""
        url = reverse('deposit:birpay_panel')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Проверяем наличие полей формы
        self.assertContains(response, f'orderconfirm_{self.order.id}')
        self.assertContains(response, f'order_action_{self.order.id}')
        self.assertContains(response, f'confirm_balance_mismatch_{self.order.id}')
        self.assertContains(response, 'data-order-id')
    
    def test_api_incoming_balance_info_returns_correct_data(self):
        """Тест: API endpoint возвращает правильные данные"""
        url = reverse('deposit:get_incoming_balance_info', args=[self.incoming_mismatch.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.content)
        self.assertEqual(data['id'], self.incoming_mismatch.id)
        self.assertEqual(data['balance'], self.incoming_mismatch.balance)
        self.assertEqual(data['check_balance'], self.incoming_mismatch.check_balance)
        self.assertTrue(data['balance_mismatch'])  # Должно быть True
    
    def test_api_incoming_balance_info_for_match(self):
        """Тест: API endpoint возвращает balance_mismatch=False для совпадающего баланса"""
        # Создаем Incoming с совпадающим балансом
        incoming_match = Incoming.objects.create(
            register_date=self.now_msk,
            response_date=self.now_msk,
            recipient='9999****9999',
            sender='Bank',
            pay=100.0,
            balance=100.0,  # Первая запись, check_balance будет None
            transaction=999993,
            type='sms',
            worker='manual',
            birpay_id=None
        )
        
        url = reverse('deposit:get_incoming_balance_info', args=[incoming_match.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.content)
        self.assertFalse(data['balance_mismatch'])  # Должно быть False

