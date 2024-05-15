import json

import structlog
from django.contrib.auth import get_user_model
from django.core.validators import EmailValidator, MinValueValidator
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import as_serializer_error
from rest_framework import validators

from payment.models import Payment, Merchant, CreditCard

logger = structlog.get_logger(__name__)


User = get_user_model()


class UserRegSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        write_only=False,
        validators=[
            EmailValidator(),
            validators.UniqueValidator(queryset=User.objects.all()),
        ],
    )
    password = serializers.CharField(
        write_only=True,
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "username",
            "password",
        )
        read_only_fields = ("id", "email")

    def validate(self, attrs: dict):
        if attrs.get("password") == "123456":
            raise ValidationError()
        return attrs

    def run_validation(self, *args, **kwargs):
        try:
            print("run_validation", *args, **kwargs)
            return super().run_validation(*args, **kwargs)
        except Exception as exc:
            print("Критическая ошибка при валидации")
            raise ValidationError(detail=as_serializer_error(exc))

    @transaction.atomic
    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data.get("first_name"),
            last_name=validated_data.get("last_name"),
        )
        return user


class PaymentCreateSerializer(serializers.ModelSerializer):
    """Создание payment"""
    amount = serializers.IntegerField(required=True, min_value=10, max_value=3000)
    confirmed_amount = serializers.IntegerField(read_only=True)
    create_at = serializers.DateTimeField(read_only=True)

    def validate(self, data):
        merchant = data.get('merchant')
        user = self.context['request'].user
        if merchant.owner != user:
            raise ValidationError('Is not your merchant')
        return data

    def save(self, **kwargs):
        return super().save(**kwargs)

    class Meta:
        model = Payment
        fields = (
            'id',
            'create_at',
            'merchant',
            'order_id',
            'amount',
            'confirmed_amount',
            'pay_type',
            'user_login',
            'owner_name',
            'status'
        )
        read_only_fields = ('status',)
        validators = [
            validators.UniqueTogetherValidator(
                queryset=Payment.objects.all(),
                fields=('merchant', 'order_id'),
                message="order_id must be Unique from user"
            ),
        ]

#
# class CardSerializer(serializers.ModelSerializer):
#     cvv = serializers.CharField(required=True, min_length=3, max_length=4)
#     class Meta:
#         model = CreditCard
#         fields = (
#             'card_number',
#             'owner_name',
#             'expired_month',
#             'expired_year',
#             'cvv'
#         )
#
#     def validate_card_number(self, data):
#         card_number = ''.join([x for x in data if x.isdigit()])
#         if all(
#                 (card_number.isdigit(), len(card_number) == 16)
#         ):
#             return card_number
#         raise ValidationError('card_number must be 16 digits')
#
#     def validate_cvv(self, data):
#         if data.isdigit():
#             return data
#         else:
#             raise ValidationError('cvv must be 3-4 digits')


class PaymentInputCardSerializer(serializers.ModelSerializer):
    """Передача данных карты для оплаты"""

    def validate_card_number(self, data):
        card_number = ''.join([x for x in data if x.isdigit()])
        print(card_number, card_number.isdigit(), len(card_number))
        if all(
                (card_number.isdigit(), len(card_number) == 16)
        ):
            return card_number
        raise ValidationError('card_number must be 16 digits')


    class Meta:
        model = CreditCard
        fields = (
            'card_number',
            'owner_name',
            'expired_month',
            'expired_year',
            'cvv'
        )


class PaymentInputSmsCodeSerializer(serializers.ModelSerializer):
    """Передача кода подтверждения из смс"""
    sms_code = serializers.CharField(min_length=4, max_length=6)
    class Meta:
        model = Payment
        fields = (
            'sms_code',
        )

class DummyDetailSerializer(serializers.Serializer):
    status = serializers.IntegerField()
    amount = serializers.CharField()


class PaymentStaffSerializer(serializers.ModelSerializer):
    sms_code = serializers.CharField(min_length=4, max_length=6, read_only=True)

    class Meta:
        fields = ('id', 'status', 'sms_code')
        model = Payment

    def validate_status(self, value):
        logger.debug(f'validate {self}')
        if self.instance.status in (-1, 9):
            raise serializers.ValidationError("Платеж уже обработан")
        return value
#
#
