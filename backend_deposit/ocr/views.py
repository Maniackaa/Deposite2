import itertools
import logging

import requests
import structlog
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count

from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView

from backend_deposit.settings import REMOTE_SERVER
from ocr.forms import ScreenForm, ScreenDeviceSelectFrom
from ocr.models import ScreenResponse
from ocr.tasks import response_parts

logger = structlog.get_logger('deposite')


class ScreenListView(ListView, PermissionRequiredMixin):

    model = ScreenResponse
    template_name = 'ocr/ScreenList.html'
    paginate_by = 10
    ordering = ('-id',)
    permission_required = ['ocr.screen_response.view']

    def get_queryset(self):
        form = ScreenDeviceSelectFrom(self.request.GET)
        form.is_valid()
        devices = form.cleaned_data.get('devices')
        return super().get_queryset().filter(source__in=devices)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = ScreenDeviceSelectFrom(self.request.GET)
        screens = self.get_queryset()
        # if not screens:
        #     return context
        all_values = range(0, 256)
        if 'button1' in self.request.GET:
            # Итоговое множество общих хороших пар (black, white)
            intersect = set(itertools.permutations(all_values, 2))
            for screen in screens:
                good_pairs = screen.good_pairs()
                screen_good_pairs = set()
                for good_pair in good_pairs:
                    pair = (good_pair.black, good_pair.white)
                    screen_good_pairs.add(pair)
                intersect = intersect & screen_good_pairs
            context['intersect'] = sorted(list(intersect))
        return context


class ScreenCreateView(PermissionRequiredMixin, CreateView):

    model = ScreenResponse
    template_name = 'ocr/ScreenCreate.html'
    form_class = ScreenForm
    success_url = reverse_lazy('ocr:screen_list')
    permission_required = ['ocr.screen_response.view']


class ScreenListDetail(PermissionRequiredMixin, UpdateView, DetailView):
    model = ScreenResponse
    template_name = 'ocr/ScreenDetail.html'
    fields = '__all__'
    success_url = reverse_lazy('ocr:screen_list')
    permission_required = ['ocr.screen_response.view']

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.save()
        screen = self.object
        pairs = screen.parts.values('black', 'white')
        ready_pairs = set((x['black'], x['white']) for x in pairs)
        all_values = range(0, 256)
        comb = list(itertools.permutations(all_values, 2))
        bad_pairs = []
        black_range = range(0, 29)
        white_range = range(0, 256)
        for black in black_range:
            for white in white_range:
                bad_pairs.append((black, white))
        logger.info(f'Распознанных частей для {screen}: {len(ready_pairs)} из {len(comb) - len(bad_pairs)}')
        if 'response_button' in self.request.POST:
            empty_pairs = []
            for pair in comb:
                if pair in ready_pairs or pair in bad_pairs:
                    continue
                empty_pairs.append(pair)

            # image = screen.image.read()
            # files = {'image': image}
            # response = requests.post(REMOTE_SERVER + '/ocr/create_screen/',
            #                          data={'name': screen.name, 'source': screen.source},
            #                          files=files,
            #                          timeout=10)
            # logger.info(response)
            # logger.info(response.json())

            logger.info(f'Добавляем в очередь нераспозанных пар: {len(empty_pairs)} шт.')
            # Создание таски по распознаванию
            response_parts.delay(screen.id, empty_pairs)
            logger.debug(f'Очередь отправлена')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # screen = ScreenResponse.objects.get(id=55)
        screen = self.object
        senders = screen.parts.values('sender').annotate(count=Count('sender')).order_by('-count')
        transactions = screen.parts.values('transaction').annotate(count=Count('transaction')).order_by('-count')
        recipients = screen.parts.values('recipient').annotate(count=Count('recipient')).order_by('-count')
        response_dates = screen.parts.values('response_date').annotate(count=Count('response_date')).order_by('-count')
        context['transactions'] = transactions
        context['recipients'] = recipients
        context['senders'] = senders
        context['response_dates'] = response_dates
        return context
