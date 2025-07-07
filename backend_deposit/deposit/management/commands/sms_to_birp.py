import datetime

from django.apps import apps
from django.core.management.base import BaseCommand
from win32comext.adsi.demos.scp import logger


class Command(BaseCommand):
    help = 'Заполняет поле incoming в BirpayOrder'

    def handle(self, *args, **options):
        BirpayOrder = apps.get_model('deposit', 'BirpayOrder')
        Incoming = apps.get_model('deposit', 'Incoming')
        orders = BirpayOrder.objects.exclude(incomingsms_id__isnull=True).exclude(incomingsms_id='')
        self.stdout.write(f'orders: {orders.count()}')

        for order in orders:
            try:
                incoming = Incoming.objects.get(pk=order.incomingsms_id)
                order.incoming = incoming
                order.save(update_fields=['incoming'])
            except Incoming.DoesNotExist:
                self.stdout.write(f'order {order} - не найдена смс')
                pass
            except Exception as e:
                pass


        incomings = Incoming.objects.filter(register_date__gte=datetime.datetime(2025, 6, 15)).exclude(birpay_id__isnull=True).exclude(birpay_id='')
        self.stdout.write(f'incomings: {incomings.count()}')
        count = 0
        for incoming in incomings:
            try:
                count += 1
                if count % 1000 == 0:
                    self.stdout.write(f'incoming {count}')
                order = BirpayOrder.objects.get(merchant_transaction_id=incoming.birpay_id)
                order.incoming = incoming
                order.save(update_fields=['incoming'])
            except BirpayOrder.DoesNotExist:
                self.stdout.write(f'incoming {incoming} - не найден')
                pass
            except Exception as e:
                pass

        self.stdout.write(self.style.SUCCESS('успешно заполнено'))
