import structlog
from rest_framework import serializers

from payment.models import Payment

logger = structlog.get_logger(__name__)


class PaymentSerializer(serializers.ModelSerializer):

    status = serializers.IntegerField(required=True)

    class Meta:
        fields = ('id', 'status')
        model = Payment

    def validate_status(self, value):
        logger.debug(f'validate {self}')
        # if value not in (4, 5, -1):
        #     raise serializers.ValidationError(
        #         "Такой статус нельзя")
        return value


