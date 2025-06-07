import datetime
import logging
import re

import structlog
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.transaction import atomic
from django.dispatch import receiver

from django.db.models.signals import post_delete, post_save, pre_save
from django.urls import reverse
from django.utils.html import format_html
from colorfield.fields import ColorField
from django_currentuser.middleware import get_current_authenticated_user
from structlog.contextvars import bind_contextvars, clear_contextvars

from backend_deposit import settings
from core.asu_pay_func import check_asu_payment_for_card, create_birpay_payment, send_card_data, send_card_data_birshop, \
    send_sms_code_birpay
from core.global_func import send_message_tg
from deposit.tasks import check_incoming
from ocr.views_api import *
from users.models import Options

logger = structlog.get_logger('deposite')
err_log = structlog.get_logger('deposite')

# User = get_user_model()


class TrashIncoming(models.Model):

    register_date = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    text = models.CharField('Текст сообщения', max_length=1000)
    worker = models.CharField(max_length=50, null=True)

    def __str__(self):
        string = f'Мусор {self.id} {self.register_date} {self.text[:20]}'
        return string

@receiver(post_save, sender=TrashIncoming)
def after_save_trash(sender, instance: TrashIncoming, **kwargs):

    try:
        """
        Передачас смс-кодов kapital в AsuPay для выплат на m10
        3DS
        Code: 1933
        1.00 AZN
        4*9412
        www.birbank.az
        NWGI9CfwoU7
        """
        pattern = r"3DS\nCode: (\d\d\d\d)\n(\d+\.\d\d) AZN\n(\d\*\d{4})\nwww.birbank.az"
    except Exception as e:
        logger.error(e, exc_info=True)

    # Поиск смс в мусоре по активным картам
    try:
        pattern = r"Code: (\d*)\n([\d,]+\.\d{2}) AZN\n(\d\*\d\d\d\d)"
        text = instance.text.replace('\r\n', '\n')
        match = re.search(pattern, text)
        if match:
            logger.debug('Мусор по шаблону OTP')
            code, raw_amount, card_mask = match.groups()
            amount = convert_atb_value(raw_amount)
            logger.debug(f'{code, raw_amount, amount, card_mask}') # '4*7498'
            first_char = card_mask[0]
            last_chars = card_mask[2:]
            logger.debug(f'Ищем карту {first_char, last_chars}')
            card = CreditCard.objects.filter(
                number__startswith=first_char,
                number__endswith=last_chars
            ).first()
            logger.debug(f'card: {card}')
            if card and card.is_active:
                logger.debug('Карта активна')
                # Проверим есть ли активные платежи по этой карте
                response = check_asu_payment_for_card(card_number=card.number)
                results = response.json().get('results', [])
                if response.status_code == 200 and len(results) == 1:
                    payment = results[0]
                    logger.info('Нужный платеж найден. Передаем смс')
                    message = (
                        f'Смс с рабочей карты {card_mask}:\n'
                        f'{amount} azn. Code: {code}'
                    )
                    send_message_tg(message=message)
                    # Проверим сумму
                    logger.info(f'Проверим сумму. сумма заявки: {card.current_payment_amount}, смс: {amount}')
                    if amount == card.current_payment_amount:
                        logger.info(f'Сумма совпадает с текущей завкой: {amount}')
                        send_sms_code_birpay(payment_id=payment["id"], sms_code=code)

                else:
                    logger.info(f'Нужный платеж не найден. result_list: {results}')
        else:
            logger.debug(logger.info(f'Не по шаблону BirPay:\n{repr(text)}'))

    except Exception as e:
        logger.error(e)


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
    birpay_id = models.CharField('id платежа с birpay', max_length=15, null=True, blank=True, db_index=True)
    comment = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        # ordering = ('id',)
        permissions = [
            ("can_hand_edit", "Может делать ручные корректировки"),
            # ("can_see_bad_warning", "Видит уведомления о новых BadScreen"),
        ]

    def get_absolute_url(self):
        return reverse('deposit:incoming_edit', kwargs={'pk': self.pk})

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


# @receiver(pre_save, sender=Incoming)
# def pre_save_withdraw(sender, instance: Incoming, raw, using, update_fields, *args, **kwargs):


@receiver(post_save, sender=Incoming)
def after_save_incoming(sender, instance: Incoming, created, **kwargs):
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

    # Обработка прихода на нашу карту
    if created:
        try:
            # Если карта в списке CreditCards и активна то создаем заявку для BirPayShop на asu
            active_cards = CreditCard.objects.filter(is_active=True).values_list('name', flat=True)
            logger.info(f'active_cards: {active_cards}')
            if instance.recipient in active_cards:
                logger.info(f'Платеж на активную карту {instance.recipient}')
                active_card = CreditCard.objects.get(name=instance.recipient)
                bind_contextvars(active_card=active_card.name)
                min_balance = 300
                if instance.balance > min_balance:
                    logger.info('Баланс больше лимита')
                    # Проверим есть ли активные платежи по этой карте
                    response = check_asu_payment_for_card(card_number=active_card.number)
                    logger.debug(f'response: {response.status_code}')
                    if response.status_code != 200:
                        raise ValueError('Плохой ответ при проверке активных платежей по карте')
                    result = response.json()
                    logger.info(f'result: {result}')
                    results = result.get('results', [])
                    logger.info(f'results: {results}')
                    if results:
                        logger.info('Есть активные платежи. Отбой')
                        return

                    logger.debug(f'Активных выплат по карте нет. Создаем новую заявку на асу')
                    #{'merchant': 34, 'order_id': 1586, 'amount': 1560.0, 'user_login': '119281059', 'pay_type': 'card_2'}
                    payment_data = {
                        'merchant': Options.load().asu_birshop_merchant_id,
                        'order_id': instance.pk,
                        'amount': instance.balance - 1,
                        'pay_type': 'card_2'}
                    p = create_birpay_payment(payment_data)
                    logger.info(f'Создана новая выплата {p}')
                    active_card.current_payment_amount = instance.balance - 1
                    active_card.save()
                    logger.debug(f'К карте {active_card} привязан {p}')
                    # Передаем данные карты:
                    card_data = {
                        "card_number": active_card.number,
                        "expired_month": active_card.expired_month,
                        "expired_year": active_card.expired_year,
                        "cvv": active_card.cvv
                    }
                    response = send_card_data_birshop(payment_id=p, card_data=card_data)
                    logger.debug(f'Результат передачи карты response: {response}')

                else:
                    logger.info(f'Сумма {instance.pay} меньше лимита {min_balance}')

                clear_contextvars()
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
    is_active = models.BooleanField(default=False)
    current_payment_amount = models.FloatField(null=True, blank=True)

    @property
    def expired_month(self):
        try:
            expired_month, expired_year = self.expire.split('/')
            return expired_month
        except Exception:
            return ''

    @property
    def expired_year(self):
        try:
            expired_month, expired_year = self.expire.split('/')
            return expired_year
        except Exception:
            return ''

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


class BirpayOrder(models.Model):
    birpay_id = models.IntegerField(unique=True, db_index=True)
    created_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField()
    merchant_transaction_id = models.CharField(max_length=16, db_index=True)
    merchant_user_id = models.CharField(max_length=16, db_index=True)
    merchant_name = models.CharField(max_length=64, null=True, blank=True)
    customer_name = models.CharField(max_length=128, null=True, blank=True)
    check_file = models.ImageField(upload_to='birpay_check', null=True, blank=True)
    check_file_url = models.URLField(null=True, blank=True)
    check_file_failed = models.BooleanField(default=False)
    status = models.SmallIntegerField()
    amount = models.FloatField()
    operator = models.CharField(max_length=128, null=True, blank=True)
    raw_data = models.JSONField()

    class Meta:
        ordering = ('-created_at',)


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
