import re

from django.core.validators import MinValueValidator, RegexValidator
from rest_framework import serializers

from .models import Incoming, Deposit


class IncomingSerializer(serializers.ModelSerializer):

    class Meta:
        fields = '__all__'
        model = Incoming

    def create(self, validated_data):
        incoming = Incoming.objects.create(**validated_data)
        return incoming


# class DepositSerializer(serializers.ModelSerializer):
#     register_time = serializers.DateTimeField(source='register_time', read_only=True)
#     phone = serializers.IntegerField(validators=[RegexValidator(regex='+994')])
#     pay_sum = serializers.IntegerField(validators=[MinValueValidator(limit_value=5)])
#
#     class Meta:
#         fields = ('phone', 'pay_sum')
#         model = Deposit
