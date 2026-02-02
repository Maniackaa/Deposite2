from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.postgres.fields import ArrayField
from django.core.mail import send_mail
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin, Group
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
import re

# from deposit.models import Message


from users.managers import UserManager


def validate_card_number(card_number):
    """
    Валидатор для номеров кредитных карт.
    Только полные 16-значные номера карт.
    Поддерживает форматы:
    - 1234567890123456 (полный номер 16 цифр)
    - 1234-5678-9012-3456 (с дефисами)
    - 1234 5678 9012 3456 (с пробелами)
    """
    if not card_number or not isinstance(card_number, str):
        raise ValidationError('Номер карты не может быть пустым')
    
    # Убираем все пробелы и дефисы
    cleaned = re.sub(r'[\s\-]', '', card_number.strip())
    
    # Проверяем, что содержит только цифры
    if not re.match(r'^\d+$', cleaned):
        raise ValidationError(
            f'Номер карты "{card_number}" должен содержать только цифры'
        )
    
    # Проверяем длину (должно быть 16 символов)
    if len(cleaned) != 16:
        raise ValidationError(
            f'Номер карты "{card_number}" должен содержать ровно 16 цифр. '
            f'Получено: {len(cleaned)} цифр'
        )
    
    return cleaned


def validate_card_numbers_list(card_numbers):
    """
    Валидатор для списка номеров карт
    """
    if not card_numbers:
        return card_numbers
    
    validated_cards = []
    for card in card_numbers:
        if card and card.strip():  # Пропускаем пустые значения
            validated_card = validate_card_number(card.strip())
            if validated_card not in validated_cards:  # Избегаем дубликатов
                validated_cards.append(validated_card)
    
    return validated_cards


class User(AbstractBaseUser, PermissionsMixin):
    username_validator = UnicodeUsernameValidator()

    USER = "user"
    STAFF = "staff"
    ADMIN = "admin"
    MODERATOR = "editor"
    AGENT = "agent"
    ROLES = (
        (USER, "Пользователь"),
        (ADMIN, "Администратор"),
        (STAFF, "Оператор"),
        (MODERATOR, "Корректировщик"),
        (AGENT, "Агент")
    )

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    # id = models.UUIDField(primary_key=True, default=uuid4, editable=False, db_index=True)

    username = models.CharField(
        max_length=150,
        unique=True,
        validators=[username_validator],
    )

    email = models.EmailField(
        verbose_name="Email-адрес",
        null=False,
        blank=False
    )

    role = models.CharField(
        max_length=20,
        choices=ROLES,
        default=USER,
    )

    objects = UserManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    is_superuser = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.username

    def get_short_name(self):
        return self.email

    def get_full_name(self):
        return self.username

    def email_user(self, subject, message, from_email=None, **kwargs):
        send_mail(subject, message, from_email, [self.email], **kwargs)

    def group(self):
        user_groups = self.groups.values('name')
        group_list = []
        for group in user_groups:
            group_list.append(group.get('name'))
        return group_list

    @admin.display(boolean=True, description='Видит уведомления?')
    def bad_warning(self):
        return self.profile.view_bad_warning


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        instance.profile = Profile.objects.create(user=instance,
                                                  my_filter=[])
    instance.profile.save()




class Profile(models.Model):
    user = models.OneToOneField(
        verbose_name="Пользователь", to=User, on_delete=models.CASCADE
    )

    first_name = models.CharField(
        verbose_name="Имя",
        max_length=30,
        null=True,
        blank=True
    )
    last_name = models.CharField(
        verbose_name="Фамилия",
        max_length=150,
        null=True,
        blank=True
    )

    my_filter = models.JSONField('Фильтр по получателю', default=list, blank=True)
    my_filter2 = models.JSONField('Фильтр по получателю2', default=list, blank=True)
    my_filter3 = models.JSONField('Фильтр по получателю3', default=list, blank=True)
    view_bad_warning = models.BooleanField(default=False)
    assigned_card_numbers = ArrayField(
        models.CharField(max_length=16), 
        blank=True, 
        default=list,
        help_text='Номера кредитных карт для мониторинга (16 цифр). Формат: 1234567890123456'
    )

    @staticmethod
    def all_message_count():
        Message = apps.get_model('deposit', "Message")
        return Message.objects.exclude(type='macros').count()

    def read_message_count(self):
        res = self.user.messages_read.all().exclude(message__type='macros').count()
        return res

    def clean(self):
        """Валидация полей модели"""
        super().clean()
        
        # Валидируем номера карт
        if self.assigned_card_numbers:
            try:
                self.assigned_card_numbers = validate_card_numbers_list(self.assigned_card_numbers)
            except ValidationError as e:
                raise ValidationError({'assigned_card_numbers': e.message})

    def save(self, *args, **kwargs):
        """Переопределяем save для автоматической валидации"""
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.user.username}'

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"
        permissions = [
            ("base2", "Только база 2"),
            ("all_base", "Все базы"),
            # ("can_see_bad_warning", "Видит уведомления о новых BadScreen"),
            ('stats', 'Статистика по картам'),
            ('graph', 'График'),
        ]


@receiver(pre_save, sender=Profile)
def track_assigned_cards_changes(sender, instance, **kwargs):
    """Отслеживает изменения в assigned_card_numbers для обновления мониторинга"""
    if instance.pk:
        try:
            old_instance = Profile.objects.get(pk=instance.pk)
            old_cards = set(old_instance.assigned_card_numbers or [])
            new_cards = set(instance.assigned_card_numbers or [])
            
            # Сохраняем изменения в атрибуте экземпляра для использования в post_save
            instance._old_assigned_cards = old_cards
            instance._new_assigned_cards = new_cards
            instance._cards_added = new_cards - old_cards
            instance._cards_removed = old_cards - new_cards
        except Profile.DoesNotExist:
            # Если профиль новый, все карты считаются добавленными
            instance._old_assigned_cards = set()
            instance._new_assigned_cards = set(instance.assigned_card_numbers or [])
            instance._cards_added = set(instance.assigned_card_numbers or [])
            instance._cards_removed = set()
    else:
        # Если профиль новый, все карты считаются добавленными
        instance._old_assigned_cards = set()
        instance._new_assigned_cards = set(instance.assigned_card_numbers or [])
        instance._cards_added = set(instance.assigned_card_numbers or [])
        instance._cards_removed = set()


@receiver(post_save, sender=Profile)
def update_card_monitoring_status(sender, instance, created, **kwargs):
    """Обновляет статус мониторинга карт при изменении assigned_card_numbers"""
    if hasattr(instance, '_cards_added') and hasattr(instance, '_cards_removed'):
        from django.apps import apps
        from django.utils import timezone
        
        CardMonitoringStatus = apps.get_model('deposit', 'CardMonitoringStatus')
        now = timezone.now()
        
        # Добавляем новые карты в мониторинг
        for card_number in instance._cards_added:
            if card_number:  # Проверяем, что карта не пустая
                CardMonitoringStatus.objects.update_or_create(
                    card_number=card_number,
                    defaults={
                        'is_active': True,  # Новые карты считаем активными
                        'last_activity': now
                    }
                )
                print(f"Добавлена карта {card_number} в мониторинг")
        
        # Удаляем карты из мониторинга
        for card_number in instance._cards_removed:
            if card_number:  # Проверяем, что карта не пустая
                CardMonitoringStatus.objects.filter(card_number=card_number).delete()
                print(f"Удалена карта {card_number} из мониторинга")


class SingletonModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.__class__.objects.exclude(id=self.id).delete()
        super(SingletonModel, self).save(*args, **kwargs)

    @classmethod
    def load(cls):
        try:
            return cls.objects.get()
        except cls.DoesNotExist:
            return cls()


class Options(SingletonModel):
    birpay_check = models.BooleanField(verbose_name='Делать проверки Birpay', default=True)
    um_login = models.CharField(default='login')
    um_password = models.CharField(default='password')
    asu_login = models.CharField(default='login')
    asu_password = models.CharField(default='password')
    asu_merchant_id = models.IntegerField(default=1)
    asu_secret = models.CharField(default='')

    asu_birshop_login = models.CharField(verbose_name='Логин для BirpayShop', default='login')
    asu_birshop_password = models.CharField(verbose_name='Пароль для магазина BirpayShop', default='password')
    asu_birshop_merchant_id = models.CharField(default=1)

    z_asu_login = models.CharField(verbose_name='Логин для Z-ASU', default='login')
    z_asu_password = models.CharField(verbose_name='Пароль для Z-ASU', default='password')

    gpt_chek_is_active = models.BooleanField(verbose_name='Делать проерку чеков GPT', default=0)
    # gpt_prompt = models.TextField(verbose_name='Запрос для чеков', default='')
    gpt_auto_approve = models.BooleanField(default=False)
    birpay_moshennik_list = ArrayField(models.CharField(max_length=1000), blank=True, default=list)
    birpay_painter_list = ArrayField(models.CharField(max_length=1000), blank=True, default=list)
    card_monitoring_minutes = models.PositiveIntegerField(verbose_name='Время мониторинга карт (минуты)', default=20)



    def __str__(self):
        return f'Options({self.birpay_check})'
