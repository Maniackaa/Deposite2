"""
Команда для создания тестовых записей Incoming с примерами правильных и неправильных расчетов баланса.
Используется для тестирования логики проверки баланса при автоподтверждении и ручной привязке.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Max
from deposit.models import Incoming
import datetime
import random


class Command(BaseCommand):
    help = 'Создает тестовые записи Incoming с примерами правильных и неправильных расчетов баланса'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clean',
            action='store_true',
            help='Удалить существующие тестовые записи перед созданием новых',
        )

    def handle(self, *args, **options):
        base_time = timezone.now()
        test_recipient_prefix = 'TEST_'
        
        if options['clean']:
            # Удаляем существующие тестовые записи
            deleted_count = Incoming.objects.filter(recipient__startswith=test_recipient_prefix).delete()[0]
            self.stdout.write(f'Удалено существующих тестовых записей: {deleted_count}')
        
        # Получаем максимальный transaction ID для генерации уникальных значений
        max_transaction = Incoming.objects.aggregate(max_id=Max('transaction'))['max_id'] or 0
        start_transaction = max_transaction + 1
        
        # Если start_transaction слишком маленький, используем случайное большое число
        if start_transaction < 900000:
            start_transaction = 900000 + random.randint(1, 99999)
        
        created_records = []
        transaction_counter = start_transaction
        
        # Сценарии для тестирования привязки:
        # Каждый сценарий включает: сумму платежа (pay) и баланс (balance)
        # Также создаем предыдущую SMS для расчета check_balance
        
        scenarios = [
            # Сценарий 1: Сумма 100.0, баланс совпадает (точное совпадение)
            {
                'name': 'MATCH_AMOUNT_100_BALANCE_OK',
                'pay': 100.0,
                'prev_balance': 1000.0,
                'balance': 1100.0,  # check_balance = 1000.0 + 100.0 = 1100.0 ✓
                'description': 'Сумма 100.0, баланс совпадает (точное)'
            },
            # Сценарий 2: Сумма 100.0, баланс совпадает (округление)
            {
                'name': 'MATCH_AMOUNT_100_BALANCE_OK_ROUND',
                'pay': 100.0,
                'prev_balance': 1000.0,
                'balance': 1100.05,  # check_balance = 1100.0, округляется до 1100.1, balance округляется до 1100.1 ✓
                'description': 'Сумма 100.0, баланс совпадает (округление)'
            },
            # Сценарий 3: Сумма 100.0, баланс НЕ совпадает
            {
                'name': 'MATCH_AMOUNT_100_BALANCE_BAD',
                'pay': 100.0,
                'prev_balance': 1000.0,
                'balance': 1100.1,  # check_balance = 1100.0, округляется до 1100.0, balance округляется до 1100.1 ✗
                'description': 'Сумма 100.0, баланс НЕ совпадает'
            },
            # Сценарий 4: Сумма 50.0, баланс совпадает
            {
                'name': 'MATCH_AMOUNT_50_BALANCE_OK',
                'pay': 50.0,
                'prev_balance': 500.0,
                'balance': 550.0,  # check_balance = 500.0 + 50.0 = 550.0 ✓
                'description': 'Сумма 50.0, баланс совпадает'
            },
            # Сценарий 5: Сумма 50.0, баланс НЕ совпадает
            {
                'name': 'MATCH_AMOUNT_50_BALANCE_BAD',
                'pay': 50.0,
                'prev_balance': 500.0,
                'balance': 550.15,  # check_balance = 550.0, округляется до 550.0, balance округляется до 550.2 ✗
                'description': 'Сумма 50.0, баланс НЕ совпадает'
            },
            # Сценарий 6: Сумма 200.0, баланс совпадает
            {
                'name': 'MATCH_AMOUNT_200_BALANCE_OK',
                'pay': 200.0,
                'prev_balance': 2000.0,
                'balance': 2200.0,  # check_balance = 2000.0 + 200.0 = 2200.0 ✓
                'description': 'Сумма 200.0, баланс совпадает'
            },
            # Сценарий 7: Сумма 200.0, баланс НЕ совпадает
            {
                'name': 'MATCH_AMOUNT_200_BALANCE_BAD',
                'pay': 200.0,
                'prev_balance': 2000.0,
                'balance': 2200.1,  # check_balance = 2200.0, округляется до 2200.0, balance округляется до 2200.1 ✗
                'description': 'Сумма 200.0, баланс НЕ совпадает'
            },
            # Сценарий 8: Сумма 238.7, баланс совпадает (для тестирования конкретного случая)
            {
                'name': 'MATCH_AMOUNT_238_7_BALANCE_OK',
                'pay': 238.7,
                'prev_balance': 1000.0,
                'balance': 1238.7,  # check_balance = 1000.0 + 238.7 = 1238.7 ✓
                'description': 'Сумма 238.7, баланс совпадает'
            },
            # Сценарий 9: Сумма 238.7, баланс НЕ совпадает
            {
                'name': 'MATCH_AMOUNT_238_7_BALANCE_BAD',
                'pay': 238.7,
                'prev_balance': 1000.0,
                'balance': 1238.8,  # check_balance = 1238.7, округляется до 1238.7, balance округляется до 1238.8 ✗
                'description': 'Сумма 238.7, баланс НЕ совпадает'
            },
            # Сценарий 10: Сумма 75.5, баланс совпадает (округление)
            {
                'name': 'MATCH_AMOUNT_75_5_BALANCE_OK_ROUND',
                'pay': 75.5,
                'prev_balance': 500.0,
                'balance': 575.55,  # check_balance = 575.5, округляется до 575.5, balance округляется до 575.6 ✗ (нет, должно быть 575.5)
                'description': 'Сумма 75.5, баланс совпадает (округление)'
            },
        ]
        
        # Исправляем сценарий 10 - баланс должен совпадать после округления
        scenarios[9]['balance'] = 575.54  # check_balance = 575.5, округляется до 575.5, balance округляется до 575.5 ✓
        
        for scenario in scenarios:
            recipient = f"{test_recipient_prefix}{scenario['name']}"
            
            # Создаем предыдущую SMS для расчета check_balance
            prev_incoming = Incoming.objects.create(
                register_date=base_time - datetime.timedelta(minutes=20),
                response_date=base_time - datetime.timedelta(minutes=20),
                recipient=recipient,
                sender='Test Bank',
                pay=50.0,  # Произвольная сумма для предыдущей SMS
                balance=scenario['prev_balance'],
                transaction=transaction_counter,
                type='sms',
                worker='test',
                birpay_id=None
            )
            created_records.append(prev_incoming)
            transaction_counter += 1
            
            # Создаем текущую SMS с указанными параметрами
            incoming = Incoming.objects.create(
                register_date=base_time - datetime.timedelta(minutes=10),
                response_date=base_time - datetime.timedelta(minutes=10),
                recipient=recipient,
                sender='Test Bank',
                pay=scenario['pay'],
                balance=scenario['balance'],
                transaction=transaction_counter,
                type='sms',
                worker='test',
                birpay_id=None
            )
            created_records.append(incoming)
            transaction_counter += 1
            
            # Проверяем совпадение баланса
            incoming.refresh_from_db()
            if incoming.check_balance is not None and incoming.balance is not None:
                check_rounded = round(float(incoming.check_balance) * 10) / 10
                balance_rounded = round(float(incoming.balance) * 10) / 10
                balance_match = check_rounded == balance_rounded
                status = '✓' if balance_match else '✗'
                self.stdout.write(
                    self.style.SUCCESS(
                        f'{status} {scenario["description"]}: ID={incoming.id}, '
                        f'pay={incoming.pay}, check_balance={incoming.check_balance}→{check_rounded}, '
                        f'balance={incoming.balance}→{balance_rounded}, '
                        f'совпадают={balance_match}'
                    )
                )
            else:
                balance_match = False
                status = '?'
                self.stdout.write(
                    self.style.WARNING(
                        f'{status} {scenario["description"]}: ID={incoming.id}, '
                        f'pay={incoming.pay}, check_balance={incoming.check_balance}, '
                        f'balance={incoming.balance}, совпадают={balance_match}'
                    )
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nВсего создано записей: {len(created_records)}\n'
                f'Сценариев: {len(scenarios)}'
            )
        )
        
        # Выводим сводку
        self.stdout.write('\nСводка по записям:')
        for record in created_records:
            if record.check_balance is not None and record.balance is not None:
                check_rounded = round(record.check_balance * 10) / 10
                balance_rounded = round(record.balance * 10) / 10
                match = check_rounded == balance_rounded
                status = '✓' if match else '✗'
                self.stdout.write(
                    f'{status} ID={record.id}, recipient={record.recipient}, '
                    f'pay={record.pay}, check_balance={record.check_balance}→{check_rounded}, '
                    f'balance={record.balance}→{balance_rounded}, '
                    f'совпадают={match}'
                )
