from django.conf import settings
from django.core.mail import send_mail
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin, Group
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from users.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    username_validator = UnicodeUsernameValidator()

    USER = "user"
    STAFF = "staff"
    ADMIN = "admin"
    MODERATOR = "editor"
    ROLES = (
        (USER, "Пользователь"),
        (ADMIN, "Администратор"),
        (STAFF, "Оператор"),
        (MODERATOR, "Корректировщик"),
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

    my_filter = models.JSONField('Фильтр по получателю', default=list)
    view_bad_warning = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.user.username}'

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

