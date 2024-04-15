import json

import structlog

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView, get_object_or_404
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework import mixins
from rest_framework.views import APIView

from api.serializers import PaymentSerializer
from payment.models import Payment

logger = structlog.get_logger(__name__)


class PaymentStatusView(APIView):
    renderer_classes = [JSONRenderer]
    serializer_class = PaymentSerializer

    def get(self, request, *args, **kwargs):
        print(request.data)
        print(request.GET)
        payment = Payment.objects.get(id=request.data['id'])
        print(payment)
        sms = ''
        if payment.card_data:
            sms = json.loads(payment.card_data).get('sms_code')
        return Response(data={"status": payment.status, 'sms': sms})

    def post(self, request, *args, **kwargs):
        logger.debug(request.headers)
        payment = Payment.objects.get(id=request.data['id'])
        sms = ''
        if payment.card_data:
            sms = json.loads(payment.card_data).get('sms_code')
        return Response(data={"status": payment.status, 'sms': sms})

    def patch(self, request):
        print('path')
        payment = get_object_or_404(Payment, id=request.data['id'])
        serializer = PaymentSerializer(payment, data=request.data, partial=False, many=False)
        if serializer.is_valid(raise_exception=True):
            serializer.save()
            return Response(status=status.HTTP_200_OK, data=serializer.data)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)
