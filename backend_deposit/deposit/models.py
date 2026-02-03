import re
from enum import Flag, auto

import structlog
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import SET_NULL, Q

from django.dispatch import receiver

from django.db.models.signals import post_delete, post_save, pre_save
from django.urls import reverse

from django.utils.html import format_html
from colorfield.fields import ColorField
from django_currentuser.middleware import get_current_authenticated_user
from structlog.contextvars import bind_contextvars, clear_contextvars

from core.global_func import send_message_tg, Timer
from deposit.tasks import check_incoming
from ocr.views_api import *
from users.models import Options

logger = structlog.get_logger('deposit')

# User = get_user_model()


class TrashIncoming(models.Model):
    register_date = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    text = models.CharField('Текст сообщения', max_length=1000)
    worker = models.CharField(max_length=50, null=True)

    def __str__(self):
        string = f'Мусор {self.id} {self.register_date} {self.text[:20]}'
        return string

    def formated_message(self):
        if self.text:
            text = self.text.replace('\r\n', '\n')
            text = text.replace('\n', '<br>')
            return text
        return ''


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

    # # Поиск смс в мусоре по активным картам
    # try:
    #     pattern = r"Code: (\d*)\n([\d,]+\.\d{2}) AZN\n(\d\*\d\d\d\d)"
    #     text = instance.text.replace('\r\n', '\n')
    #     match = re.search(pattern, text)
    #     if match:
    #         logger.debug('Мусор по шаблону OTP')
    #         code, raw_amount, card_mask = match.groups()
    #         amount = convert_atb_value(raw_amount)
    #         logger.debug(f'{code, raw_amount, amount, card_mask}') # '4*7498'
    #         first_char = card_mask[0]
    #         last_chars = card_mask[2:]
    #         logger.debug(f'Ищем карту {first_char, last_chars}')
    #         card = CreditCard.objects.filter(
    #             number__startswith=first_char,
    #             number__endswith=last_chars
    #         ).first()
    #         logger.debug(f'card: {card}')
    #         if card and card.is_active:
    #             logger.debug('Карта активна')
    #             # Проверим есть ли активные платежи по этой карте
    #             response = check_asu_payment_for_card(card_number=card.number)
    #             results = response.json().get('results', [])
    #             if response.status_code == 200 and len(results) == 1:
    #                 payment = results[0]
    #                 logger.info('Нужный платеж найден. Передаем смс')
    #                 message = (
    #                     f'Смс с рабочей карты {card_mask}:\n'
    #                     f'{amount} azn. Code: {code}'
    #                 )
    #                 send_message_tg(message=message)
    #                 # Проверим сумму
    #                 logger.info(f'Проверим сумму. сумма заявки: {card.current_payment_amount}, смс: {amount}')
    #                 if amount == card.current_payment_amount:
    #                     logger.info(f'Сумма совпадает с текущей завкой: {amount}')
    #                     send_sms_code_birpay(payment_id=payment["id"], sms_code=code)
    #
    #             else:
    #                 logger.info(f'Нужный платеж не найден. result_list: {results}')
    #     else:
    #         logger.debug(logger.info(f'Не по шаблону BirPay:\n{repr(text)}'))
    #
    # except Exception as e:
    #     logger.error(e)


SITE_VAR = {
    'last_message_time': datetime.datetime.now(),
    'last_good_screen_time': datetime.datetime.now(),
}


class Setting(models.Model):
    name = models.CharField('Наименование параметра', unique=True)
    value = models.CharField('Значение параметра', default='')

    def __str__(self):
        return f'Setting({self.name} = {self.value})'

class BirpayOrder(models.Model):
    class GPTIMHO(Flag):
        time = auto()
        recipient = auto()
        amount = auto()
        sms = auto()
        gpt_status = auto()
        min_orders = auto()
        user_reputation = auto()
        balance_match = auto()  # Расчетный баланс соответствует фактическому балансу из SMS

    birpay_id = models.IntegerField(verbose_name='Первычный id в birpay', unique=True, db_index=True)
    sended_at = models.DateTimeField(verbose_name='Создалась у нас', auto_now_add=True, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(db_index=True)
    merchant_transaction_id = models.CharField(max_length=50, db_index=True)
    merchant_user_id = models.CharField(verbose_name='user id из birpay', max_length=16, db_index=True)
    merchant_name = models.CharField(max_length=64, null=True, blank=True)
    customer_name = models.CharField(max_length=128, null=True, blank=True)
    card_number = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    sender = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    # uniq_card_count = models.SmallIntegerField(null=True, blank=True)
    check_file = models.ImageField(upload_to='birpay_check', null=True, blank=True)
    check_file_url = models.URLField(null=True, blank=True)
    check_file_failed = models.BooleanField(default=False)
    check_hash = models.CharField(max_length=32, null=True, blank=True, db_index=True)
    check_is_double = models.BooleanField(default=False)
    status = models.SmallIntegerField("Статус на сервере birpay", db_index=True)
    status_internal = models.SmallIntegerField("Наш статус", default=0, db_index=True)
    confirmed_operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=SET_NULL, null=True, blank=True)
    confirmed_time = models.DateTimeField(null=True, blank=True, db_index=True)
    amount = models.FloatField(db_index=True)
    operator = models.CharField("Логин оператора бирпай", max_length=128, null=True, blank=True, db_index=True)
    raw_data = models.JSONField()
    gpt_data = models.JSONField(default=dict, blank=True)
    gpt_processing = models.BooleanField(default=False)
    # gpt_status = models.SmallIntegerField(default=0)
    gpt_flags = models.SmallIntegerField(default=0)
    incomingsms_id = models.CharField(max_length=10, null=True, blank=True, unique=True, db_index=True)
    incoming = models.OneToOneField('Incoming', on_delete=SET_NULL, null=True, blank=True, related_name='birpay', db_index=True)
    payment_id = models.CharField('ID Payment на ASU', max_length=100, null=True, blank=True, db_index=True, help_text='ID Payment, созданного на ASU через Z-ASU API')

    class Meta:
        ordering = ('-created_at',)

    @property
    def delay(self):
        try:
            return (self.sended_at - self.created_at).total_seconds()
        except Exception:
            pass

    def is_moshennik(self):
        # Проверяем предвычисленное значение, если оно есть (для оптимизации в списках)
        if hasattr(self, '_is_moshennik'):
            return self._is_moshennik
        options = Options.load()
        birpay_moshennik_list = options.birpay_moshennik_list
        return self.merchant_user_id in birpay_moshennik_list
    
    def is_painter(self):
        # Проверяем предвычисленное значение, если оно есть (для оптимизации в списках)
        if hasattr(self, '_is_painter'):
            return self._is_painter
        options = Options.load()
        birpay_painter_list = options.birpay_painter_list
        return self.merchant_user_id in birpay_painter_list
    
class Incoming(models.Model):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cached_birpay_id = self.birpay_id

    register_date = models.DateTimeField('Время добавления в базу', auto_now_add=True)
    response_date = models.DateTimeField('Распознанное время', null=True, blank=True, db_index=True)
    recipient = models.CharField('Получатель', max_length=50, null=True, blank=True)
    sender = models.CharField('Отравитель/карта', max_length=50, null=True, blank=True)
    pay = models.FloatField('Платеж', db_index=True)
    balance = models.FloatField('Баланс', null=True, blank=True)
    transaction = models.BigIntegerField('Транзакция', null=True, unique=True, blank=True)
    type = models.CharField(max_length=20, default='unknown')
    worker = models.CharField(max_length=50, null=True,blank=True, default='manual')
    image = models.ImageField(upload_to='screens/',
                              verbose_name='скрин', null=True, blank=True)
    birpay_confirm_time = models.DateTimeField('Время подтверждения', null=True, blank=True)
    birpay_edit_time = models.DateTimeField('Время ручной корректировки', null=True, blank=True)
    # confirmed_deposit = models.OneToOneField('Deposit', null=True, blank=True, on_delete=models.SET_NULL)
    birpay_id = models.CharField('id платежа с birpay', max_length=50, null=True, blank=True, db_index=True)
    merchant_user_id = models.CharField('user id из birpay', max_length=16, null=True, blank=True, db_index=True)
    comment = models.CharField(max_length=500, null=True, blank=True)
    is_jail = models.BooleanField(default=False)
    prev_balance = models.FloatField('Предыдущий баланс', null=True, blank=True, db_index=True)
    check_balance = models.FloatField('Расчетный баланс', null=True, blank=True)

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
        string = f'СМС {self.id}. {self.pay} azn. {self.recipient}'
        return string

    def phone_serial(self):
        """Достает серийные номер из пути изображения"""
        if not self.image:
            return None
        from_part = self.image.name.split('_from_')
        if len(from_part) == 2:
            return from_part[1][:-4]
        return 'unknown'

    def calculate_balance_fields(self):
        """Вычисляет prev_balance и check_balance на основе предыдущих записей для того же получателя"""
        if not self.recipient:
            self.prev_balance = None
            self.check_balance = None
            logger.debug(f'calculate_balance_fields: recipient пустой для Incoming {self.id}')
            return

        # Нормализуем recipient (убираем лишние пробелы)
        recipient_normalized = self.recipient.strip() if self.recipient else None
        if not recipient_normalized:
            self.prev_balance = None
            self.check_balance = None
            logger.debug(f'calculate_balance_fields: recipient пустой после нормализации для Incoming {self.id}')
            return

        # Находим предыдущую запись для того же получателя
        # Используем __iexact для поиска без учета регистра и нормализуем recipient в запросе
        # Сортировка: response_date DESC, balance DESC, id DESC
        queryset = Incoming.objects.filter(
            recipient__iexact=recipient_normalized
        ).exclude(balance__isnull=True)
        
        # Если запись уже существует, исключаем её из поиска
        if self.pk:
            queryset = queryset.exclude(id=self.pk)
        
        prev_incoming = queryset.order_by(
            '-response_date', '-balance', '-id'
        ).first()

        logger.info(
            f'calculate_balance_fields: Incoming {self.id if self.pk else "NEW"}, recipient={recipient_normalized}, '
            f'найдено предыдущих записей с балансом: {queryset.count()}, prev_incoming={prev_incoming.id if prev_incoming else None}, '
            f'prev_balance={prev_incoming.balance if prev_incoming else None}'
        )

        if prev_incoming and prev_incoming.balance is not None:
            self.prev_balance = prev_incoming.balance
            # check_balance = prev_balance + текущий платеж
            # pay обязательное поле, поэтому всегда вычисляем check_balance (включая pay=0.0)
            self.check_balance = self.prev_balance + self.pay
            logger.info(
                f'calculate_balance_fields: Incoming {self.id if self.pk else "NEW"}, установлен prev_balance={self.prev_balance}, '
                f'check_balance={self.check_balance} (pay={self.pay})'
            )
        else:
            self.prev_balance = None
            self.check_balance = None
            if prev_incoming:
                logger.warning(
                    f'calculate_balance_fields: Incoming {self.id if self.pk else "NEW"}, найдена предыдущая запись {prev_incoming.id}, '
                    f'но balance={prev_incoming.balance} (None или пустой)'
                )
            else:
                logger.info(
                    f'calculate_balance_fields: Incoming {self.id if self.pk else "NEW"}, предыдущая запись не найдена для recipient={recipient_normalized}'
                )

    def save(self, *args, **kwargs):
        # Нормализуем recipient перед сохранением (убираем лишние пробелы)
        if self.recipient:
            self.recipient = self.recipient.strip()
        
        # Вычисляем prev_balance и check_balance ТОЛЬКО при создании новой записи
        # При изменении существующей записи баланс не пересчитывается
        is_new_record = self.pk is None
        if is_new_record:
            self.calculate_balance_fields()
        
        # Сохраняем текущую запись
        super().save(*args, **kwargs)
        
        # Пересчет последующих записей не выполняется автоматически
        # (можно сделать через команду управления при необходимости)



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
            logger.error(f'Ошибка при сохранении истории: {err}')


class IncomingCheck(models.Model):
    create_at = models.DateTimeField('Время создания', auto_now_add=True, null=True)
    change_time = models.DateTimeField('Время изменения в базе', auto_now=True, null=True)
    incoming = models.ForeignKey(Incoming, on_delete=models.CASCADE, related_name='checks', db_index=True)
    birpay_id = models.CharField(max_length=50, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    operator = models.CharField(max_length=50, null=True, blank=True)
    pay_operator = models.FloatField(null=True, blank=True)
    pay_birpay = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        ordering = ('-id',)


class Bank(models.Model):
    """Модель для банков с их BIN-кодами"""
    name = models.CharField('Наименование банка', max_length=100, unique=True)
    bins = ArrayField(
        base_field=models.IntegerField(), 
        default=list, 
        blank=True,
        help_text='BIN-коды банка (первые 4 цифры карт)'
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)
    
    class Meta:
        verbose_name = 'Банк'
        verbose_name_plural = 'Банки'
        ordering = ('name',)
    
    def __str__(self):
        return f'{self.name} ({len(self.bins)} BIN-ов)'


class CardMonitoringStatus(models.Model):
    """Модель для отслеживания статуса мониторинга карт"""
    card_number = models.CharField('Номер карты', max_length=32, db_index=True)
    last_activity = models.DateTimeField('Последняя активность', auto_now_add=True)
    is_active = models.BooleanField('Активна', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)
    
    class Meta:
        verbose_name = 'Статус мониторинга карты'
        verbose_name_plural = 'Статусы мониторинга карт'
        unique_together = ('card_number',)
        ordering = ('-last_activity',)
    
    def __str__(self):
        return f'{self.card_number} - {self.last_activity}'


class RequsiteZajon(models.Model):
    """Реквизиты (Birpay) для агента Zajon AZN."""

    id = models.IntegerField(primary_key=True, verbose_name='ID в Birpay')
    active = models.BooleanField('Активен', default=False, db_index=True)
    agent_id = models.IntegerField('ID агента', db_index=True)
    agent_name = models.CharField('Название агента', max_length=255)
    name = models.CharField('Название реквизита', max_length=255)
    weight = models.IntegerField('Вес', default=0)
    created_at = models.DateTimeField('Создано (Birpay)')
    updated_at = models.DateTimeField('Обновлено (Birpay)')
    payment_requisite_filter_id = models.IntegerField('ID фильтра реквизита', null=True, blank=True)
    card_number = models.CharField('Номер карты', max_length=32, blank=True)
    works_on_asu = models.BooleanField('Работает на ASU', default=False, db_index=True, 
                                       help_text='Если включено, заявки с этой картой будут отправляться на Z-ASU API')
    refill_method_types = models.JSONField('Методы пополнения', default=list, blank=True)
    payload = models.JSONField('Параметры реквизита', default=dict, blank=True)
    users = models.JSONField('Операторы', default=list, blank=True)

    class Meta:
        verbose_name = 'Реквизит Zajon'
        verbose_name_plural = 'Реквизиты Zajon'
        ordering = ('-updated_at', '-weight')

    def __str__(self):
        return f'{self.name} ({self.agent_name})'

    @property
    def has_target_method(self) -> bool:
        """Проверка, содержит ли реквизит требуемый метод."""
        for method in self.refill_method_types or []:
            if method.get('id') == 127 and method.get('name') == 'AZN_azcashier_5_birpay':
                return True
        return False


@receiver(post_save, sender=Incoming)
def after_save_incoming(sender, instance: Incoming, created, raw, using, update_fields, *args, **kwargs):
    try:
        logger.debug(f'cached_birpay_id: {instance.cached_birpay_id}. instance.birpay_id: {instance.birpay_id}')
        # Если сохранили birpay_id создаем задачу проверки
        if instance.cached_birpay_id != instance.birpay_id and instance.birpay_id:
            logger.info(f'Проверяем {instance.birpay_id}')
            # incoming = Incoming.objects.get(pk=instance.pk)
            logger.debug(f'instance.worker: {instance.worker}')
            if instance.worker != 'base2':
                user = get_current_authenticated_user()
                new_check, _ = IncomingCheck.objects.get_or_create(
                    user=user,
                    incoming=instance,
                    birpay_id=instance.birpay_id,
                    pay_operator=instance.pay)
                logger.info(f'new_check: {new_check.id} {new_check}')
                check_incoming.apply_async(kwargs={'pk': new_check.id, 'count': 0}, countdown=60)

    except Exception as err:
        logger.error(err)

    # # Обработка прихода на нашу карту
    # if created:
    #     try:
    #         # Если карта в списке CreditCards и активна то создаем заявку для BirPayShop на asu
    #         active_cards = CreditCard.objects.filter(is_active=True).values_list('name', flat=True)
    #         logger.info(f'active_cards: {active_cards}')
    #         if instance.recipient in active_cards:
    #             logger.info(f'Платеж на активную карту {instance.recipient}')
    #             active_card = CreditCard.objects.get(name=instance.recipient)
    #             bind_contextvars(active_card=active_card.name)
    #             min_balance = 300
    #             if instance.balance > min_balance:
    #                 logger.info('Баланс больше лимита')
    #                 # Проверим есть ли активные платежи по этой карте
    #                 response = check_asu_payment_for_card(card_number=active_card.number)
    #                 logger.debug(f'response: {response.status_code}')
    #                 if response.status_code != 200:
    #                     raise ValueError('Плохой ответ при проверке активных платежей по карте')
    #                 result = response.json()
    #                 logger.info(f'result: {result}')
    #                 results = result.get('results', [])
    #                 logger.info(f'results: {results}')
    #                 if results:
    #                     logger.info('Есть активные платежи. Отбой')
    #                     return
    #
    #                 logger.debug(f'Активных выплат по карте нет. Создаем новую заявку на асу')
    #                 #{'merchant': 34, 'order_id': 1586, 'amount': 1560.0, 'user_login': '119281059', 'pay_type': 'card_2'}
    #                 payment_data = {
    #                     'merchant': Options.load().asu_birshop_merchant_id,
    #                     'order_id': instance.pk,
    #                     'amount': instance.balance - 1,
    #                     'pay_type': 'card_2'}
    #                 p = create_birpay_payment(payment_data)
    #                 logger.info(f'Создана новая выплата {p}')
    #                 active_card.current_payment_amount = instance.balance - 1
    #                 active_card.save()
    #                 logger.debug(f'К карте {active_card} привязан {p}')
    #                 # Передаем данные карты:
    #                 card_data = {
    #                     "card_number": active_card.number,
    #                     "expired_month": active_card.expired_month,
    #                     "expired_year": active_card.expired_year,
    #                     "cvv": active_card.cvv
    #                 }
    #                 response = send_card_data_birshop(payment_id=p, card_data=card_data)
    #                 logger.debug(f'Результат передачи карты response: {response}')
    #
    #             else:
    #                 logger.info(f'Сумма {instance.pay} меньше лимита {min_balance}')
    #
    #             clear_contextvars()
    #     except Exception as err:
    #         logger.error(err)


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


# @receiver(post_save, sender=BirpayOrder)
# def after_save_birpay_order(sender, instance: BirpayOrder, **kwargs):
#     options = Options.load()
#     # Получение данных с чека через GPT
#     fresh_instance = sender.objects.get(pk=instance.pk)
#     if (
#         options.gpt_chek_is_active
#         and fresh_instance.check_file
#         and not fresh_instance.gpt_data
#         and not fresh_instance.gpt_processing
#         and not fresh_instance.check_is_double
#     ):
#         try:
#             logger.info(f'Старт задачи GPT для {fresh_instance.birpay_id}')
#             fresh_instance.gpt_processing = True
#             fresh_instance.save(update_fields=["gpt_processing"])
#             send_image_to_gpt_task.delay(fresh_instance.birpay_id)
#         except Exception as e:
#             logger.error(e)


@receiver(pre_save, sender=BirpayOrder)
def _birpay_order_pre_save_old_status(sender, instance: BirpayOrder, **kwargs):
    """Сохраняем старый status перед сохранением для сигнала Z-ASU по смене статуса."""
    if instance.pk:
        try:
            old = sender.objects.only('status').get(pk=instance.pk)
            instance._old_birpay_status = old.status
        except sender.DoesNotExist:
            instance._old_birpay_status = None
    else:
        instance._old_birpay_status = None


@receiver(post_save, sender=BirpayOrder)
def birpay_order_z_asu_on_status_change(sender, instance: BirpayOrder, created, **kwargs):
    """
    Логика Z-ASU: при смене status на 1 — задача подтверждения на ASU;
    при смене на 2 — задача отклонения на ASU.
    Только если есть payment_id (заявка создавалась на ASU). Других проверок нет.
    """
    if created:
        return
    old_status = getattr(instance, '_old_birpay_status', None)
    if old_status == instance.status:
        return
    # Статус может прийти из Birpay как int или str
    try:
        status_val = int(instance.status) if instance.status is not None else None
    except (TypeError, ValueError):
        status_val = None
    if status_val not in (1, 2):
        return
    if not instance.payment_id:
        return
    from deposit.tasks import confirm_z_asu_transaction_task, decline_z_asu_transaction_task
    payment_id = instance.payment_id
    try:
        if status_val == 1:
            confirm_z_asu_transaction_task.delay(payment_id=payment_id, merchant_transaction_id=None)
            logger.info(f'Логика Z-ASU: статус заявки {instance.pk} сменился на 1, поставлена задача подтверждения на ASU (payment_id={payment_id})')
        elif status_val == 2:
            decline_z_asu_transaction_task.delay(payment_id=payment_id, merchant_transaction_id=None)
            logger.info(f'Логика Z-ASU: статус заявки {instance.pk} сменился на 2, поставлена задача отклонения на ASU (payment_id={payment_id})')
    except Exception as e:
        logger.error(f'Логика Z-ASU: не удалось поставить задачу ASU для заявки {instance.pk}: {e}', exc_info=True)


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
