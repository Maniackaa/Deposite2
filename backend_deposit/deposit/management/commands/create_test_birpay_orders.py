"""
Команда для создания тестовых BirpayOrder записей с различными суммами
для тестирования привязки к Incoming записям.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Max
from deposit.models import BirpayOrder
import datetime
import random


class Command(BaseCommand):
    help = 'Создает тестовые BirpayOrder записи с различными суммами для тестирования привязки'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clean',
            action='store_true',
            help='Удалить существующие тестовые записи перед созданием новых',
        )

    def handle(self, *args, **options):
        base_time = timezone.now()
        
        # Тестовые данные
        # Используем идентификаторы, которые точно не будут в списке мошенников
        test_merchant_user_ids = ['TEST_DEV_USER_1', 'TEST_DEV_USER_2', 'TEST_DEV_USER_3']
        test_card_numbers = ['4189800086664404', '4189800086664405', '4189800086664406']
        test_merchant_names = ['Test Merchant 1', 'Test Merchant 2', 'Test Merchant 3']
        
        # Если нужно очистить старые тестовые записи
        if options.get('clean'):
            deleted_count = BirpayOrder.objects.filter(
                merchant_user_id__startswith='TEST_'
            ).delete()[0]
            self.stdout.write(self.style.WARNING(f'Удалено {deleted_count} тестовых записей'))
        
        # Получаем максимальный birpay_id для генерации новых уникальных ID
        max_birpay_id = BirpayOrder.objects.aggregate(max_id=Max('birpay_id'))['max_id'] or 0
        start_birpay_id = max_birpay_id + 1
        
        # Создаем заказы с различными суммами, соответствующими тестовым SMS
        # Суммы должны совпадать и не совпадать с суммами в create_test_incomings
        scenarios = [
            # Заказы с суммами, совпадающими с SMS
            {'amount': 100.0, 'description': 'Сумма 100.0 (совпадает с SMS)'},
            {'amount': 50.0, 'description': 'Сумма 50.0 (совпадает с SMS)'},
            {'amount': 200.0, 'description': 'Сумма 200.0 (совпадает с SMS)'},
            {'amount': 238.7, 'description': 'Сумма 238.7 (совпадает с SMS)'},
            {'amount': 75.5, 'description': 'Сумма 75.5 (совпадает с SMS)'},
            
            # Заказы с суммами, НЕ совпадающими с SMS (для тестирования проверки суммы)
            {'amount': 150.0, 'description': 'Сумма 150.0 (НЕ совпадает с SMS 100.0)'},
            {'amount': 75.0, 'description': 'Сумма 75.0 (НЕ совпадает с SMS 50.0)'},
            {'amount': 250.0, 'description': 'Сумма 250.0 (НЕ совпадает с SMS 200.0)'},
            {'amount': 300.0, 'description': 'Сумма 300.0 (НЕ совпадает с SMS 238.7)'},
            {'amount': 100.0, 'description': 'Сумма 100.0 (НЕ совпадает с SMS 75.5)'},
            
            # Дополнительные заказы для разнообразия
            {'amount': 25.0, 'description': 'Сумма 25.0'},
            {'amount': 500.0, 'description': 'Сумма 500.0'},
            {'amount': 99.99, 'description': 'Сумма 99.99'},
            {'amount': 150.5, 'description': 'Сумма 150.5'},
        ]
        
        created_count = 0
        
        for i, scenario in enumerate(scenarios):
            birpay_id = start_birpay_id + i
            # Формат merchant_transaction_id: числовой формат (например, 160515, 160586)
            # Генерируем 6-значное число, начиная с 100000
            merchant_transaction_id = str(100000 + birpay_id)
            merchant_user_id = random.choice(test_merchant_user_ids)
            card_number = random.choice(test_card_numbers)
            merchant_name = random.choice(test_merchant_names)
            
            amount = scenario['amount']
            
            # Генерируем случайное время создания (в пределах последних 7 дней)
            days_ago = random.randint(0, 7)
            hours_ago = random.randint(0, 23)
            created_at = base_time - datetime.timedelta(days=days_ago, hours=hours_ago)
            updated_at = created_at + datetime.timedelta(minutes=random.randint(1, 60))
            
            # Статусы: 0 - pending (ожидает), 1 - approve (подтвержден), 2 - decline (отклонен)
            # Для тестирования создаем в основном со статусом 0 (pending), чтобы можно было менять статус
            status = random.choice([0, 0, 0, 0, 1, 2])  # Больше записей со статусом 0 (pending)
            
            # Создаем raw_data (имитация данных от Birpay API)
            raw_data = {
                'id': birpay_id,
                'createdAt': created_at.isoformat(),
                'updatedAt': updated_at.isoformat(),
                'merchantTransactionId': merchant_transaction_id,
                'merchantUserId': merchant_user_id,
                'amount': str(amount),
                'status': status,
                'merchant': {
                    'name': merchant_name
                },
                'customerName': f'Test Customer {i+1}',
                'paymentRequisite': {
                    'payload': {
                        'card_number': card_number
                    }
                }
            }
            
            try:
                order = BirpayOrder.objects.create(
                    birpay_id=birpay_id,
                    created_at=created_at,
                    updated_at=updated_at,
                    merchant_transaction_id=merchant_transaction_id,
                    merchant_user_id=merchant_user_id,
                    merchant_name=merchant_name,
                    customer_name=f'Test Customer {i+1}',
                    card_number=card_number,
                    sender=card_number[-4:] if card_number else None,
                    status=status,
                    status_internal=0,
                    amount=amount,
                    operator=f'TestOperator{i+1}',
                    raw_data=raw_data,
                    gpt_data={},
                    gpt_processing=False,
                    gpt_flags=0,
                )
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Создан BirpayOrder: ID={order.id}, birpay_id={order.birpay_id}, '
                        f'MerchTxID={order.merchant_transaction_id}, amount={order.amount} - {scenario["description"]}'
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Ошибка при создании записи {birpay_id}: {e}'))
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nУспешно создано {created_count} из {len(scenarios)} записей BirpayOrder\n'
                f'Суммы совпадающие с SMS: 100.0, 50.0, 200.0, 238.7, 75.5\n'
                f'Суммы НЕ совпадающие с SMS: 150.0, 75.0, 250.0, 300.0, 100.0 (для SMS 75.5)'
            )
        )
