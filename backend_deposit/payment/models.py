import logging
import uuid

import requests
import structlog
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from deposit.models import Incoming

logger = structlog.get_logger(__name__)


class Shop(models.Model):
    name = models.CharField('Название', max_length=100)
    is_active = models.BooleanField(default=False)
    host = models.URLField(null=True, blank=True)
    secret = models.CharField('Секретная фраза', max_length=100, null=True, blank=True)
    # Endpoints
    pay_success_endpoint = models.URLField(null=True, blank=True)


class CreditCard(models.Model):
    card_number = models.CharField('Номер карты', max_length=16)
    owner_name = models.CharField('Имя владельца', max_length=100)
    cvv = models.CharField(max_length=4, null=True, blank=True)
    card_type = models.CharField('Система карты', max_length=100)
    card_bank = models.CharField('Название банка', max_length=50, default='Bank')
    expired_month = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    expired_year = models.IntegerField(validators=[MinValueValidator(2023), MaxValueValidator(2100)])
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


class PayRequisite(models.Model):
    pay_type = models.CharField('Тип реквизитов',
                                      choices=[('Card-to-Card', 'Card-to-Card')])
    card = models.ForeignKey(CreditCard, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=False)
    info = models.CharField('Инструкция', null=True, blank=True)

    def __repr__(self):
        string = f'{self.__class__.__name__}({self.id})'
        return string

    def __str__(self):
        string = f'{self.pay_type} {self.id}. {self.card}'
        return string


class Payment(models.Model):
    PAYMENT_STATUS = (
        (0, 'Заготовка'),
        (1, 'Ожидание'),
        (2, 'Подтвержден'),
        (-1, 'Отклонен')
    )

    def __init__(self, *args, **kwargs) -> None:
        # logger.debug(f'__init__ Payment {args} {kwargs}')
        super().__init__(*args, **kwargs)
        self.cached_status = self.status

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, max_length=36, db_index=True, unique=True,)
    shop = models.ForeignKey('Shop', on_delete=models.CASCADE, null=True)
    order_id = models.CharField(max_length=36, db_index=True, unique=True, null=True, blank=True)
    user_login = models.CharField(max_length=36)
    amount = models.IntegerField('Сумма заявки', validators=[MinValueValidator(5)])
    pay_requisite = models.ForeignKey('PayRequisite', on_delete=models.CASCADE, null=True)
    screenshot = models.ImageField(upload_to='uploaded_pay_screens/',
                      verbose_name='Ваша квитанция', null=True, blank=True, help_text='Приложите скриншот квитанции после оплаты')

    create_at = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    status = models.IntegerField('Статус депозита',
                                 default=0,
                                 choices=PAYMENT_STATUS)
    change_time = models.DateTimeField('Время изменения в базе', auto_now=True)
    confirmed_time = models.DateTimeField('Время подтверждения', null=True, blank=True)

    # Данные отправителя
    phone = models.CharField('Телефон отправителя', max_length=20, null=True, blank=True)
    referrer = models.URLField('Откуда пришел', null=True, blank=True)

    # Подтверждение:
    confirmed_incoming = models.OneToOneField(verbose_name='Платеж', to=Incoming,
                                    on_delete=models.SET_NULL, null=True, blank=True)
    confirmed_amount = models.IntegerField('Подтвержденная сумма заявки', null=True, blank=True)
    comment = models.CharField('Комментарий', max_length=1000, null=True, blank=True)

    def __str__(self):
        string = f'{self.__class__.__name__} {self.id}.'
        return string

    def status_str(self):
        for status_num, status_str in self.PAYMENT_STATUS:
            if status_num == self.status:
                return status_str

    def short_id(self):
        return f'{str(self.id)[-6:]}'

    class Meta:
        ordering = ('-create_at',)


# @receiver(pre_save, sender=Payment)
# def pre_save_pay(sender, instance: Payment, raw, using, update_fields, *args, **kwargs):
#     logger.debug(f'pre_save_status = {instance.status} cashed: {instance.cached_status}')


@receiver(post_save, sender=Payment)
def after_save_pay(sender, instance: Payment, created, raw, using, update_fields, *args, **kwargs):
    logger.debug(f'post_save_status = {instance.status}  cashed: {instance.cached_status}')
    # Если статус изменился с 2 на 3 (потвержден):
    if instance.status == 2 and instance.cached_status == 1:
        logger.debug('Выполняем действие полсле подтверждения платежа')
