"""
Команда для пересчета prev_balance и check_balance для всех существующих записей Incoming.
Используется после добавления полей prev_balance и check_balance в модель.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from deposit.models import Incoming


class Command(BaseCommand):
    help = 'Пересчитывает prev_balance и check_balance для всех записей Incoming'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Количество записей для обработки за раз',
        )
        parser.add_argument(
            '--recipient',
            type=str,
            default=None,
            help='Пересчитать только для указанного получателя (recipient)',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        recipient_filter = options.get('recipient')

        # Получаем все записи, отсортированные по recipient, response_date, balance, id
        queryset = Incoming.objects.all().order_by('recipient', '-response_date', '-balance', '-id')
        
        if recipient_filter:
            queryset = queryset.filter(recipient=recipient_filter)
            self.stdout.write(f'Пересчет для получателя: {recipient_filter}')
        
        total_count = queryset.count()
        self.stdout.write(f'Всего записей для пересчета: {total_count}')

        processed = 0
        errors = 0

        # Обрабатываем батчами
        for i in range(0, total_count, batch_size):
            batch = queryset[i:i + batch_size]
            
            with transaction.atomic():
                for incoming in batch:
                    try:
                        # Вызываем метод расчета балансов
                        incoming.calculate_balance_fields()
                        # Сохраняем только поля балансов для оптимизации
                        incoming.save(update_fields=['prev_balance', 'check_balance'])
                        processed += 1
                        
                        if processed % 100 == 0:
                            self.stdout.write(f'Обработано: {processed}/{total_count}')
                    except Exception as e:
                        errors += 1
                        self.stdout.write(
                            self.style.ERROR(f'Ошибка при обработке Incoming {incoming.id}: {e}')
                        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Пересчет завершен. Обработано: {processed}, Ошибок: {errors}'
            )
        )

