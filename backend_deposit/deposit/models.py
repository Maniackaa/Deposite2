import datetime
import logging
import re

from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.transaction import atomic
from django.dispatch import receiver

from django.db.models.signals import post_delete, post_save
from django.utils.html import format_html
from colorfield.fields import ColorField

from backend_deposit.settings import TZ

logger = logging.getLogger(__name__)

User = get_user_model()


class TrashIncoming(models.Model):

    register_date = models.DateTimeField('Время добавления в базу', auto_now=True)
    text = models.CharField('Текст сообщения', max_length=1000)
    worker = models.CharField(max_length=50, null=True)

    def __str__(self):
        string = f'Мусор {self.id} {self.register_date} {self.text[:20]}'
        return string


class Incoming(models.Model):

    register_date = models.DateTimeField('Время добавления в базу', auto_now=True)
    response_date = models.DateTimeField('Распознанное время', null=True, blank=True)
    recipient = models.CharField('Получатель', max_length=50, null=True, blank=True)
    sender = models.CharField('Отравитель/карта', max_length=50, null=True, blank=True)
    pay = models.FloatField('Платеж')
    balance = models.FloatField('Баланс', null=True, blank=True)
    transaction = models.IntegerField('Транзакция', null=True, unique=True, blank=True)
    type = models.CharField(max_length=20, default='unknown')
    worker = models.CharField(max_length=50, null=True)
    image = models.ImageField(upload_to='screens/',
                              verbose_name='скрин', null=True, blank=True)
    birpay_confirm_time = models.DateTimeField('Время подтверждения', null=True, blank=True)
    birpay_edit_time = models.DateTimeField('Время ручной корректировки', null=True, blank=True)
    confirmed_deposit = models.OneToOneField('Deposit', null=True, blank=True, on_delete=models.SET_NULL)
    birpay_id = models.CharField('id платежа с birpay', max_length=15, null=True, blank=True)

    class Meta:
        permissions = [
            ("can_hand_edit", "Может делать ручные корректировки"),
        ]

    def __iter__(self):
        for field in self._meta.fields:
            yield field.verbose_name, field.value_to_string(self)

    def __str__(self):
        string = f'Платеж {self.id}. Сумма: {self.pay}. {self.transaction}.  Депозит: {self.confirmed_deposit.id if self.confirmed_deposit else "-"}'
        return string

    # @property
    # def color_back(self):
    #     bank_color_back = ColorBank.objects.filter(name=self.sender).first()
    #     if self.type in ['m10', 'm10_short']:
    #         return '#80FFFF'
    #     if bank_color_back:
    #         return bank_color_back.color_back
    #     return '#FFFFFF'
    #
    # @property
    # def color_font(self):
    #     bank_color_font = ColorBank.objects.filter(name=self.sender).first()
    #     if self.type in ['m10', 'm10_short']:
    #         return '#000000'
    #     if bank_color_font:
    #         return bank_color_font.color_font
    #     return '#000000'


class IncomingChange(models.Model):
    time = models.DateTimeField(auto_now=True)
    incoming = models.ForeignKey(Incoming, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='incoming_changes')
    val_name = models.CharField('Имя поля старое')
    new_val = models.CharField('Старое значение', null=True)


class Deposit(models.Model):
    uid = models.CharField(max_length=36, db_index=True, unique=True, null=True, blank=True)
    register_time = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    change_time = models.DateTimeField('Время изменения в базе', auto_now=True)
    phone = models.CharField('Телефон отправителя')
    pay_sum = models.IntegerField('Сумма платежа', validators=[MinValueValidator(5)])
    input_transaction = models.IntegerField('Введенная транзакция с чека',
                                            null=True, blank=True, help_text='Введите транзакцию из чека',
                                            validators=[MinValueValidator(50000000), MaxValueValidator(99999999)])
    status = models.CharField('Статус депозита',
                              default='pending',
                              choices=[
                                  ('pending', 'На рассмотрении'),
                                  ('approved', 'Подтвержден')])
    pay_screen = models.ImageField(upload_to='pay_screens/',
                                   verbose_name='Чек об оплате', null=True, blank=True, help_text='Скриншот чека')
    confirmed_incoming = models.OneToOneField(Incoming, null=True, blank=True, on_delete=models.SET_NULL,
                                              help_text='Подтвержденный чек')

    def __str__(self):
        string = f'Депозит {self.id}. {self.input_transaction}. Сумма: {self.pay_sum}. Pay_screen: {self.pay_screen}. Наш чек: {self.confirmed_incoming.id if self.confirmed_incoming else "-"}'
        return string


class BadScreen(models.Model):
    name = models.CharField(unique=False, max_length=200)
    image = models.ImageField(upload_to='bad_screens/',
                              verbose_name='скрин')
    incoming_time = models.DateTimeField('Время добавления в базу', auto_now=True)
    worker = models.CharField(max_length=50, null=True)
    transaction = models.IntegerField('Транзакция', null=True, unique=True, blank=True)
    type = models.CharField(max_length=20, default='unknown')

    def size(self):
        return f'{self.image.size // 1024} Кб' or None


class ColorBank(models.Model):
    name = models.CharField(unique=True, max_length=100)
    color_back = ColorField(default='#FF0000')
    color_font = ColorField(default='#000000')

    def example(self):
        return format_html(
            f'<span style="color:  {self.color_font}; background: {self.color_back}">{self.name}</span>'
        )


@receiver(post_delete, sender=BadScreen)
def bad_screen_image_delete(sender, instance, **kwargs):
    if instance.image.name:
        instance.image.delete(False)


@receiver(post_delete, sender=Incoming)
def screen_image_delete(sender, instance, **kwargs):
    if instance.image.name:
        instance.image.delete(False)
#
#
# @receiver(post_save, sender=Incoming)
# def after_save_incoming(sender, instance: Incoming, **kwargs):
#     try:
#         if instance.confirmed_deposit:
#             logger.debug('incoming post_save return')
#             return
#         logger.debug(f'Действие после сохранения корректного скрина: {instance}')
#         pay = instance.pay
#         transaction = instance.transaction
#         transaction_list = [transaction - 1, transaction + 1, transaction + 2]
#         treshold = datetime.datetime.now(tz=TZ) - datetime.timedelta(minutes=10)
#         logger.debug(f'Ищем депозиты не позднее чем: {str(treshold)}')
#         deposits = Deposit.objects.filter(
#             status='pending',
#             pay_sum=pay,
#             register_time__gte=treshold,
#             input_transaction__in=transaction_list
#         ).all()
#         logger.debug(f'Найденные deposits: {deposits}')
#         if deposits:
#             deposit = deposits.first()
#             logger.debug(f'Подтверждаем депозит {deposit}')
#             deposit.confirmed_incoming = instance
#             deposit.status = 'confirmed'
#             deposit.save()
#             logger.debug(f'Депозит подтвержден: {deposit}')
#             logger.debug(f'Сохраняем confirmed_deposit: {deposit}')
#             instance.confirmed_deposit = deposit
#             instance.save()
#
#     except Exception as err:
#         logger.error(err, exc_info=True)
#
#
# @transaction.atomic
# @receiver(post_save, sender=Deposit)
# def after_save_deposit(sender, instance: Deposit, **kwargs):
#     try:
#         logger.debug(f'Действие после сохранения депозита: {instance}')
#         logger.debug(f'sender: {sender}')
#         if instance.input_transaction and instance.status == 'pending':
#             treshold = datetime.datetime.now(tz=TZ) - datetime.timedelta(minutes=10)
#             logger.debug(f'Ищем скрины не позднее чем: {str(treshold)}')
#             logger.debug(f'input_transaction: {instance.input_transaction}, {type(instance.input_transaction)}')
#             transaction_list = [instance.input_transaction - 1,
#                                 instance.input_transaction + 1,
#                                 instance.input_transaction + 2]
#             logger.debug(f'transaction_list: {transaction_list}')
#             incomings = Incoming.objects.filter(
#                 register_date__gte=treshold,
#                 pay=instance.pay_sum,
#                 transaction__in=transaction_list,
#                 confirmed_deposit=None
#             ).order_by('-id').all()
#             logger.debug(f'Найденные скрины: {incomings}')
#             if incomings:
#                 incoming = incomings.first()
#                 incoming.confirmed_deposit = instance
#                 instance.status = 'approved'
#                 incoming.save()
#                 instance.save()
#         else:
#             logger.debug('deposit post_save return')
#
#     except Exception as err:
#         logger.error(err, exc_info=True)
