import datetime
import logging
import re

import structlog
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.transaction import atomic
from django.dispatch import receiver

from django.db.models.signals import post_delete, post_save
from django.utils.html import format_html
from colorfield.fields import ColorField
from django_currentuser.middleware import get_current_authenticated_user

from backend_deposit import settings
from core.birpay_func import find_birpay_from_id
from deposit.tasks import check_incoming

logger = structlog.get_logger(__name__)
err_log = logging.getLogger(__name__)

# User = get_user_model()


class TrashIncoming(models.Model):

    register_date = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    text = models.CharField('Текст сообщения', max_length=1000)
    worker = models.CharField(max_length=50, null=True)

    def __str__(self):
        string = f'Мусор {self.id} {self.register_date} {self.text[:20]}'
        return string


SITE_VAR = {
    'last_message_time': datetime.datetime.now(),
    'last_good_screen_time': datetime.datetime.now(),
}


class Setting(models.Model):
    name = models.CharField('Наименование параметра', unique=True)
    value = models.CharField('Значение параметра', default='')

    def __str__(self):
        return f'Setting({self.name} = {self.value})'


class Incoming(models.Model):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cached_birpay_id = self.birpay_id

    register_date = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    response_date = models.DateTimeField('Распознанное время', null=True, blank=True)
    recipient = models.CharField('Получатель', max_length=50, null=True, blank=True)
    sender = models.CharField('Отравитель/карта', max_length=50, null=True, blank=True)
    pay = models.FloatField('Платеж')
    balance = models.FloatField('Баланс', null=True, blank=True)
    transaction = models.BigIntegerField('Транзакция', null=True, unique=True, blank=True)
    type = models.CharField(max_length=20, default='unknown')
    worker = models.CharField(max_length=50, null=True,blank=True, default='manual')
    image = models.ImageField(upload_to='screens/',
                              verbose_name='скрин', null=True, blank=True)
    birpay_confirm_time = models.DateTimeField('Время подтверждения', null=True, blank=True)
    birpay_edit_time = models.DateTimeField('Время ручной корректировки', null=True, blank=True)
    confirmed_deposit = models.OneToOneField('Deposit', null=True, blank=True, on_delete=models.SET_NULL)
    birpay_id = models.CharField('id платежа с birpay', max_length=15, null=True, blank=True)
    comment = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        # ordering = ('id',)
        permissions = [
            ("can_hand_edit", "Может делать ручные корректировки"),
            # ("can_see_bad_warning", "Видит уведомления о новых BadScreen"),
        ]

    def __iter__(self):
        for field in self._meta.fields:
            yield field.verbose_name, field.value_to_string(self)

    def __str__(self):
        string = f'Платеж {self.id}. Сумма: {self.pay} ({self.balance}). {self.transaction}.  Депозит: {self.confirmed_deposit.id if self.confirmed_deposit else "-"}'
        return string

    def phone_serial(self):
        """Достает серийные номер из пути изображения"""
        if not self.image:
            return None
        from_part = self.image.name.split('_from_')
        if len(from_part) == 2:
            return from_part[1][:-4]
        return 'unknown'


class IncomingChange(models.Model):
    time = models.DateTimeField(auto_now_add=True)
    incoming = models.ForeignKey(Incoming, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='incoming_changes')
    val_name = models.CharField('Имя поля')
    new_val = models.CharField('Новое значение', null=True)

    @staticmethod
    def save_incoming_history(old_incoming, new_incoming, user):
        # Сохраняем историю
        try:
            if old_incoming.birpay_id != new_incoming.birpay_id:
                new_birpay_id = IncomingChange(
                    incoming=new_incoming,
                    user=user,
                    val_name='birpay_id',
                    new_val=new_incoming.birpay_id
                )
                new_birpay_id.save()
            if old_incoming.comment != new_incoming.comment:
                new_comment = IncomingChange(
                    incoming=new_incoming,
                    user=user,
                    val_name='comment',
                    new_val=new_incoming.comment
                )
                new_comment.save()
        except Exception as err:
            err_log.error(f'Ошибка при сохранении истории: {err}')


class IncomingCheck(models.Model):
    create_at = models.DateTimeField('Время создания', auto_now_add=True, null=True)
    change_time = models.DateTimeField('Время изменения в базе', auto_now=True, null=True)
    incoming = models.ForeignKey(Incoming, on_delete=models.CASCADE, related_name='checks')
    birpay_id = models.CharField(max_length=50)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    operator = models.CharField(max_length=50, null=True, blank=True)
    pay_operator = models.FloatField(null=True, blank=True)
    pay_birpay = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        ordering = ('-id',)


@receiver(post_save, sender=Incoming)
def after_save_incoming(sender, instance: Incoming, **kwargs):
    try:
        # Если сохранили birpay_id создаем задачу проверки
        if instance.cached_birpay_id != instance.birpay_id and instance.birpay_id:
            logger.info(f'Проверяем {instance.birpay_id}')
            incoming = Incoming.objects.get(pk=instance.pk)
            if incoming.worker != 'base2':
                user = get_current_authenticated_user()
                new_check, _ = IncomingCheck.objects.get_or_create(
                    user=user,
                    incoming=incoming,
                    birpay_id=incoming.birpay_id,
                    pay_operator=incoming.pay)
                logger.info(f'new_check: {new_check.id} {new_check}')
                check_incoming.apply_async(kwargs={'pk': new_check.id, 'count': 0}, countdown=60)
    except Exception as err:
        logger.error(err)


class Deposit(models.Model):
    uid = models.CharField(max_length=36, db_index=True, unique=True, null=True, blank=True)
    register_time = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    change_time = models.DateTimeField('Время изменения в базе', auto_now=True)
    phone = models.CharField('Телефон отправителя')
    pay_sum = models.IntegerField('Сумма платежа', validators=[MinValueValidator(5)])
    input_transaction = models.BigIntegerField('Введенная транзакция с чека',
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
    incoming_time = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    worker = models.CharField(max_length=50, null=True)
    transaction = models.BigIntegerField('Транзакция', null=True, unique=True, blank=True)
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


class CreditCard(models.Model):
    name = models.CharField(unique=True, max_length=50)
    number = models.CharField(unique=True, max_length=19, default='', blank=True)
    expire = models.CharField(max_length=10, default='', blank=True)
    cvv = models.CharField(max_length=10, default='', blank=True)
    status = models.CharField(max_length=20, default='', blank=True)
    text = models.CharField(max_length=100, default='', blank=True)


class UmTransaction(models.Model):
    order_id = models.CharField(unique=True, max_length=10)
    payment_id = models.CharField(unique=True, max_length=36, null=True, blank=True)
    create_at = models.DateTimeField(auto_now_add=True)
    status = models.IntegerField(default=0)

    def __str__(self):
        return f'{self.id}. {self.order_id}: {self.payment_id}'


class WithdrawTransaction(models.Model):
    # Выводы с бирпэй
    withdraw_id = models.CharField(unique=True, max_length=36, null=True, blank=True)
    create_at = models.DateTimeField(auto_now_add=True)
    status = models.IntegerField(default=0)

    def __str__(self):
        return f'{self.id}.{self.withdraw_id}'


class Message(models.Model):
    MESSAGE_TYPES = (
        ('admin', 'От админа'),
        ('to_all', 'Для всех'),
        ('macros', 'Работа макроса')
    )

    type = models.CharField(choices=MESSAGE_TYPES, default='to_all')
    title = models.CharField(max_length=100, null=True, blank=True)
    text = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='messages')
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Message {self.id} от {self.author}: {self.title}'


class MessageRead(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='reads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='messages_read')
    time_read = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.id}. {self.message.id} прочитано {self.user}'


class RePattern(models.Model):
    pattern = models.CharField(max_length=256)
    name = models.CharField(max_length=32)

    def __str__(self):
        return self.name


@receiver(post_delete, sender=BadScreen)
def bad_screen_image_delete(sender, instance, **kwargs):
    if instance.image.name:
        instance.image.delete(False)


@receiver(post_delete, sender=Incoming)
def screen_image_delete(sender, instance, **kwargs):
    if instance.image.name:
        instance.image.delete(False)


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
