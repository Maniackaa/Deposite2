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
        payment = Payment.objects.get(id=request.data['id'])
        print(payment)
        return Response(data={"status": {payment.status}})

    def patch(self, request):
        print('path')
        payment = get_object_or_404(Payment, id=request.data['id'])
        serializer = PaymentSerializer(payment, data=request.data, partial=False, many=False)
        if serializer.is_valid(raise_exception=True):
            serializer.save()
            return Response(status=status.HTTP_200_OK, data=serializer.data)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)
