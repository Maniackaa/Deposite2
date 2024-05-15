import json

import structlog
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse, extend_schema_view

from rest_framework import viewsets, status, generics, serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action, api_view
from rest_framework.generics import GenericAPIView, get_object_or_404, CreateAPIView, UpdateAPIView
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework import mixins
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from api.permissions import PaymentOwnerOrStaff, IsStaff
from api.serializers import UserRegSerializer, PaymentCreateSerializer, PaymentInputCardSerializer, \
    PaymentInputSmsCodeSerializer, DummyDetailSerializer, PaymentStaffSerializer
from core.global_func import hash_gen
from payment.models import Payment
from payment.task import send_merch_webhook
from payment.views import get_phone_script, get_bank_from_bin

logger = structlog.get_logger(__name__)


User = get_user_model()


# @extend_schema(tags=['Users App'])
# class UsersViewSet(
#     mixins.CreateModelMixin,
#     mixins.ListModelMixin,
#     mixins.RetrieveModelMixin,
#     viewsets.GenericViewSet,
# ):
#     serializer_class = UserRegSerializer
#     queryset = User.objects.all()
#     permission_classes = (AllowAny,)
#
#     def retrieve(self, request, *args, **kwargs):
#         instance = self.get_object()
#         serializer = self.get_serializer(instance)
#         return Response(serializer.data)
#
#
# @extend_schema(tags=['Users App'])
# class RegView(CreateAPIView):
#     permission_classes = (AllowAny,)
#     serializer_class = UserRegSerializer
#
#     def post(self, request, *args, **kwargs):
#         return super().post(request, *args, **kwargs)


class ResponseCreate(serializers.Serializer):
    id = serializers.CharField()


class BadResponse(serializers.Serializer):
    errors = serializers.DictField()


class ResponseInputCard(serializers.Serializer):
    sms_required = serializers.BooleanField()
    instruction = serializers.CharField()
    bank_icon = serializers.URLField()
    signature = serializers.CharField(help_text='hash("sha256", $card_number+$secret_key)')


class ResponseInputSms(serializers.Serializer):
    status = serializers.CharField()


class PaymentViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentCreateSerializer
    queryset = Payment.objects.all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [PaymentOwnerOrStaff]

    @extend_schema(tags=['Payment check'])
    def retrieve(self, request, *args, **kwargs):
        """Просмотр данных о платеже"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(data={'id': instance.id, 'status': instance.status}, status=status.HTTP_200_OK)

    @extend_schema(tags=['Payment process'], request=PaymentCreateSerializer, summary="Создание платежа",
                   description='Отправка данных для создания платежа',

                   responses={
                       status.HTTP_201_CREATED: OpenApiResponse(
                           description='Created',
                           response=ResponseCreate,
                           examples=[
                               OpenApiExample(
                                   "Good example",
                                   value={"id": "4caed007-2d31-489c-9f3d-a2af6ccf07e4"},
                                   status_codes=[201],
                                   response_only=False,
                               ),
                           ]),

                       status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                           response=BadResponse,
                           description='Some errors',
                           examples=[
                               OpenApiExample(
                                   "Bad response",
                                   value={
                                       "amount": ["Ensure this value is greater than or equal to 1."]},
                                   status_codes=[400],
                                   response_only=False,
                               ),
                           ]),

                   },
                   )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid(raise_exception=True):
            self.perform_create(serializer)
            headers = self.get_success_headers(serializer.data)
            return Response({'status': 'success', 'id': serializer.data['id']}, status=status.HTTP_201_CREATED, headers=headers)
        return Response({'status': 'error'}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(tags=['Payment process'], summary="Отправка даных карты",
                   request=PaymentInputCardSerializer,
                   responses={status.HTTP_200_OK: OpenApiResponse(
                           response=PaymentInputCardSerializer,
                           examples=[
                               OpenApiExample(
                                   "Good example",
                                   value={
    "sms_required": False,
    "instruction": "Ödənişi təstiqləmək üçün Leobank mobil tədbiqində Sizə bildiriş gələcək. Zəhmət olmasa, Leobank mobil tədbiqinə keçid edin və köçürməni təstiq edin.",
    "bank_icon": "http://127.0.0.1:8000/media/bank_icons/leo_C6uBNoS.jpg",
    "signature": "1bc6b5702f4fdce1f93590dc9a561aafbb227307b988ffd1c5e564ebef7ee9f6"
},
                                   status_codes=[200],
                                   response_only=False,
                               ),
                           ]),

                       status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                           response=BadResponse,
                           description='Some errors',
                           examples=[
                               OpenApiExample(
                                   "Bad response",
                                   value={"expired_month": ["Ensure this value is less than or equal to 12."]},
                                   status_codes=[400],
                                   response_only=False,
                               ),
                           ]),

                   }
                   )
    @action(detail=True,
            methods=["PUT"],
            permission_classes=[PaymentOwnerOrStaff],)
    def send_card_data(self, request, *args, **kwargs):
        payment = get_object_or_404(Payment, id=self.kwargs.get("pk"))
        serializer = PaymentInputCardSerializer(data=request.data)
        if serializer.is_valid():
            card_data = serializer.validated_data
            card_number = serializer.validated_data.get('card_number')
            phone_script = get_phone_script(card_number)
            payment.phone_script_data = phone_script.data_json()
            payment.status = 3
            bank = get_bank_from_bin(card_number[:6])
            url = request.build_absolute_uri(bank.image.url)
            sms_required = phone_script.step_2_required
            payment.card_data = json.dumps(card_data)
            payment.save()
            signature = hash_gen(card_number, payment.merchant.secret)
            return Response(data={
                'sms_required': sms_required,
                'instruction': bank.instruction,
                'bank_icon': url,
                'signature': signature
            }, status=status.HTTP_200_OK)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(tags=['Payment process'],
                   request=PaymentInputSmsCodeSerializer,
                   summary="Отправка кода подтверждения",
                   description="Необходим если sms_required=True",
                   responses={status.HTTP_200_OK: OpenApiResponse(
                       response=ResponseInputSms,
                       examples=[
                           OpenApiExample(
                               "example1",
                               value={
                                   "status": "success",
                               },
                               status_codes=[200],
                               response_only=False,
                           ),
                       ])},
                   )
    @action(detail=True,
            methods=["PUT"],
            permission_classes=[PaymentOwnerOrStaff],)
    def send_sms_code(self, request, *args, **kwargs):
        payment = get_object_or_404(Payment, id=self.kwargs.get("pk"))
        serializer = PaymentInputSmsCodeSerializer(data=request.data)
        if serializer.is_valid():
            card_data = json.loads(payment.card_data)
            card_data['sms_code'] = serializer.validated_data.get('sms_code')
            payment.card_data = json.dumps(card_data)
            payment.save()
            return Response(data={'status': 'success'}, status=status.HTTP_200_OK)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PaymentInputCard(mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentInputCardSerializer
    queryset = Payment.objects.all()
    permission_classes = [PaymentOwnerOrStaff]
    http_method_names = ['put']


class PaymentInputSmsCode(mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentInputSmsCodeSerializer
    queryset = Payment.objects.all()
    permission_classes = [PaymentOwnerOrStaff]
    http_method_names = ['patch']




# class CsrfExemptSessionAuthentication(SessionAuthentication):
#     def enforce_csrf(self, request):
#         return None


class PaymentStatusView(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentStaffSerializer
    queryset = Payment.objects.all()
    permission_classes = [IsStaff]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        if instance.card_data:
            print(instance.card_data)
            sms = json.loads(instance.card_data).get('sms_code')
            data.update({'sms_code': sms})
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    # def retrieve(self, request, *args, **kwargs):
    #     print(request.data)
    #     payment = Payment.objects.filter(id=request.data['id']).first()
    #     print(payment)
    #     sms = ''
    #     if payment and payment.card_data:
    #         sms = json.loads(payment.card_data).get('sms_code')
    #         return Response(data={"status": payment.status, 'sms': sms})
    #
    #     return Response(data={"status": payment.status})

