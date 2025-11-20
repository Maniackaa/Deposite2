"""
Тесты для проверки логики расчета prev_balance и check_balance в модели Incoming.
"""
import pytest
from django.test import TestCase
from django.utils import timezone
from datetime import datetime, timedelta
from deposit.models import Incoming


@pytest.mark.django_db
class TestPrevBalanceCalculation(TestCase):
    """Тесты расчета prev_balance и check_balance"""
    
    def setUp(self):
        """Подготовка тестовых данных"""
        self.recipient1 = "1234567890123456"
        self.recipient2 = "9876543210987654"
        self.base_time = timezone.now()
    
    def test_first_incoming_no_prev_balance(self):
        """Тест: первая запись для получателя не имеет prev_balance"""
        incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Первая запись не должна иметь prev_balance
        self.assertIsNone(incoming.prev_balance)
        self.assertIsNone(incoming.check_balance)
    
    def test_second_incoming_has_prev_balance(self):
        """Тест: вторая запись должна иметь prev_balance из первой"""
        # Создаем первую запись
        first_incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем вторую запись
        second_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1200.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Вторая запись должна иметь prev_balance из первой
        self.assertEqual(second_incoming.prev_balance, 1000.0)
        # check_balance = prev_balance + pay = 1000 + 200 = 1200
        self.assertEqual(second_incoming.check_balance, 1200.0)
    
    def test_prev_balance_by_recipient(self):
        """Тест: prev_balance учитывает только записи для того же получателя"""
        # Создаем записи для получателя 1
        incoming1_recipient1 = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        incoming2_recipient1 = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1200.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Создаем запись для получателя 2
        incoming1_recipient2 = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=5),
            response_date=self.base_time + timedelta(minutes=5),
            recipient=self.recipient2,
            sender="Sender3",
            pay=300.0,
            balance=5000.0,
            transaction=333333,
            type='sms',
            worker='manual'
        )
        
        # Запись для получателя 2 не должна иметь prev_balance из записей получателя 1
        self.assertIsNone(incoming1_recipient2.prev_balance)
        self.assertIsNone(incoming1_recipient2.check_balance)
        
        # Запись для получателя 1 должна иметь prev_balance из предыдущей записи получателя 1
        incoming2_recipient1.refresh_from_db()
        self.assertEqual(incoming2_recipient1.prev_balance, 1000.0)
    
    def test_sorting_by_response_date_desc(self):
        """Тест: prev_balance берется из записи с более поздним response_date"""
        # Создаем запись с более ранним response_date
        early_incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем запись с более поздним response_date
        late_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=20),
            response_date=self.base_time + timedelta(minutes=20),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1500.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Создаем третью запись с промежуточным response_date
        middle_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender3",
            pay=300.0,
            balance=2000.0,
            transaction=333333,
            type='sms',
            worker='manual'
        )
        
        # Третья запись должна иметь prev_balance из записи с более поздним response_date
        # (late_incoming идет первым в сортировке DESC)
        middle_incoming.refresh_from_db()
        self.assertEqual(middle_incoming.prev_balance, 1500.0)
        self.assertEqual(middle_incoming.check_balance, 1500.0 + 300.0)
    
    def test_sorting_by_balance_desc_when_same_date(self):
        """Тест: при одинаковом response_date используется баланс DESC"""
        same_date = self.base_time
        
        # Создаем запись с меньшим балансом
        low_balance = Incoming.objects.create(
            register_date=same_date,
            response_date=same_date,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем запись с большим балансом
        high_balance = Incoming.objects.create(
            register_date=same_date,
            response_date=same_date,
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=2000.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Создаем третью запись
        third_incoming = Incoming.objects.create(
            register_date=same_date,
            response_date=same_date,
            recipient=self.recipient1,
            sender="Sender3",
            pay=300.0,
            balance=2500.0,
            transaction=333333,
            type='sms',
            worker='manual'
        )
        
        # Третья запись должна иметь prev_balance из записи с большим балансом
        # (high_balance идет первым в сортировке balance DESC)
        third_incoming.refresh_from_db()
        self.assertEqual(third_incoming.prev_balance, 2000.0)
        self.assertEqual(third_incoming.check_balance, 2000.0 + 300.0)
    
    def test_sorting_by_id_desc_when_same_date_and_balance(self):
        """Тест: при одинаковых response_date и balance используется id DESC"""
        same_date = self.base_time
        same_balance = 1000.0
        
        # Создаем запись с меньшим id
        first_incoming = Incoming.objects.create(
            register_date=same_date,
            response_date=same_date,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=same_balance,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем запись с большим id
        second_incoming = Incoming.objects.create(
            register_date=same_date,
            response_date=same_date,
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=same_balance,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Создаем третью запись
        third_incoming = Incoming.objects.create(
            register_date=same_date,
            response_date=same_date,
            recipient=self.recipient1,
            sender="Sender3",
            pay=300.0,
            balance=same_balance,
            transaction=333333,
            type='sms',
            worker='manual'
        )
        
        # Третья запись должна иметь prev_balance из записи с большим id
        # (second_incoming идет первым в сортировке id DESC)
        third_incoming.refresh_from_db()
        self.assertEqual(third_incoming.prev_balance, same_balance)
        self.assertEqual(third_incoming.check_balance, same_balance + 300.0)
    
    def test_no_recipient_no_prev_balance(self):
        """Тест: запись без получателя не имеет prev_balance"""
        incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=None,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        self.assertIsNone(incoming.prev_balance)
        self.assertIsNone(incoming.check_balance)
    
    def test_empty_recipient_no_prev_balance(self):
        """Тест: запись с пустым получателем не имеет prev_balance"""
        incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient="",
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        self.assertIsNone(incoming.prev_balance)
        self.assertIsNone(incoming.check_balance)
    
    def test_prev_incoming_without_balance(self):
        """Тест: если предыдущая запись не имеет баланса, prev_balance = None"""
        # Создаем запись без баланса
        incoming_no_balance = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=None,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем вторую запись
        second_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1200.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Вторая запись не должна иметь prev_balance, т.к. первая не имеет баланса
        self.assertIsNone(second_incoming.prev_balance)
        self.assertIsNone(second_incoming.check_balance)
    
    def test_check_balance_calculation(self):
        """Тест: check_balance = prev_balance + pay"""
        # Создаем первую запись
        first_incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем вторую запись с другим pay
        second_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=250.0,
            balance=1250.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # check_balance = prev_balance (1000) + pay (250) = 1250
        self.assertEqual(second_incoming.check_balance, 1250.0)
    
    def test_check_balance_with_zero_pay(self):
        """Тест: если pay = 0.0, check_balance = prev_balance + 0 = prev_balance"""
        # Создаем первую запись
        first_incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем вторую запись с pay = 0.0
        second_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=0.0,  # pay = 0, но check_balance должен быть вычислен
            balance=1000.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Если pay = 0, check_balance = prev_balance + 0 = prev_balance
        self.assertEqual(second_incoming.check_balance, 1000.0)
    
    def test_update_incoming_does_not_recalculate_balance(self):
        """Тест: при обновлении записи НЕ пересчитываются prev_balance и check_balance (балансы вычисляются только при создании)"""
        # Создаем первую запись
        first_incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем вторую запись
        second_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1200.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Проверяем начальные значения (вычислены при создании)
        self.assertEqual(second_incoming.prev_balance, 1000.0)
        self.assertEqual(second_incoming.check_balance, 1200.0)
        
        # Изменяем баланс первой записи
        first_incoming.balance = 1500.0
        first_incoming.save()
        
        # Обновляем вторую запись
        second_incoming.save()
        
        # Вторая запись НЕ должна иметь обновленный prev_balance (балансы не пересчитываются при обновлении)
        second_incoming.refresh_from_db()
        self.assertEqual(second_incoming.prev_balance, 1000.0)  # Остается прежним
        self.assertEqual(second_incoming.check_balance, 1200.0)  # Остается прежним
    
    def test_multiple_incomings_chain(self):
        """Тест: цепочка из нескольких записей для одного получателя"""
        # Создаем цепочку записей
        incoming1 = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        incoming2 = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1200.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        incoming3 = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=20),
            response_date=self.base_time + timedelta(minutes=20),
            recipient=self.recipient1,
            sender="Sender3",
            pay=300.0,
            balance=1500.0,
            transaction=333333,
            type='sms',
            worker='manual'
        )
        
        # Проверяем цепочку
        incoming2.refresh_from_db()
        incoming3.refresh_from_db()
        
        # Вторая запись ссылается на первую
        self.assertEqual(incoming2.prev_balance, 1000.0)
        self.assertEqual(incoming2.check_balance, 1200.0)
        
        # Третья запись ссылается на вторую
        self.assertEqual(incoming3.prev_balance, 1200.0)
        self.assertEqual(incoming3.check_balance, 1500.0)
    
    def test_exclude_current_record_from_search(self):
        """Тест: текущая запись исключается из поиска prev_balance"""
        # Создаем первую запись
        first_incoming = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=1000.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Создаем вторую запись
        second_incoming = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=10),
            response_date=self.base_time + timedelta(minutes=10),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=1200.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Обновляем вторую запись - она не должна ссылаться сама на себя
        second_incoming.balance = 1300.0
        second_incoming.save()
        
        # Проверяем, что prev_balance все еще ссылается на первую запись
        second_incoming.refresh_from_db()
        self.assertEqual(second_incoming.prev_balance, 1000.0)
        # check_balance должен быть пересчитан с учетом нового баланса, но prev_balance остается прежним
        self.assertEqual(second_incoming.check_balance, 1000.0 + 200.0)
    
    def test_complex_sorting_scenario(self):
        """Тест: сложный сценарий с разными датами, балансами и id"""
        # Создаем записи в разном порядке
        # Запись 1: ранняя дата, малый баланс
        incoming1 = Incoming.objects.create(
            register_date=self.base_time,
            response_date=self.base_time,
            recipient=self.recipient1,
            sender="Sender1",
            pay=100.0,
            balance=500.0,
            transaction=111111,
            type='sms',
            worker='manual'
        )
        
        # Запись 2: поздняя дата, большой баланс
        incoming2 = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=30),
            response_date=self.base_time + timedelta(minutes=30),
            recipient=self.recipient1,
            sender="Sender2",
            pay=200.0,
            balance=2000.0,
            transaction=222222,
            type='sms',
            worker='manual'
        )
        
        # Запись 3: средняя дата, средний баланс
        incoming3 = Incoming.objects.create(
            register_date=self.base_time + timedelta(minutes=15),
            response_date=self.base_time + timedelta(minutes=15),
            recipient=self.recipient1,
            sender="Sender3",
            pay=300.0,
            balance=1000.0,
            transaction=333333,
            type='sms',
            worker='manual'
        )
        
        # Проверяем: incoming3 должна ссылаться на incoming2 (более поздняя дата)
        incoming3.refresh_from_db()
        self.assertEqual(incoming3.prev_balance, 2000.0)
        self.assertEqual(incoming3.check_balance, 2000.0 + 300.0)
        
        # incoming2 должна ссылаться на incoming1 (единственная более ранняя)
        incoming2.refresh_from_db()
        self.assertEqual(incoming2.prev_balance, 500.0)
        self.assertEqual(incoming2.check_balance, 500.0 + 200.0)

