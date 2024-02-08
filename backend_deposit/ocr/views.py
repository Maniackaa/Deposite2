import itertools
import logging
import time

import requests
from django.conf import settings
from django.db.models import Count, Window, OuterRef, Q, F
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView


from ocr.forms import ScreenForm
from ocr.models import ScreenResponse
from ocr.tasks import add_response_part_to_queue

logger = logging.getLogger(__name__)


class ScreenListView(ListView):

    model = ScreenResponse
    template_name = 'ocr/ScreenList.html'
    paginate_by = 10
    ordering = ('-id',)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        screens = ScreenResponse.objects.all()
        all_values = range(0, 256)
        # Итоговое множество общих хороших пар (black, white)
        intersect = set(itertools.permutations(all_values, 2))
        start = time.perf_counter()
        for screen in screens:
            good_pairs = screen.good_pairs()
            screen_good_pairs = set()
            for good_pair in good_pairs:
                pair = (good_pair.black, good_pair.white)
                screen_good_pairs.add(pair)
            print(time.perf_counter() - start)
            intersect = intersect & screen_good_pairs
            print(time.perf_counter() - start)
        print()
        print(time.perf_counter() - start)
        context['intersect'] = sorted(list(intersect))
        return context


class ScreenCreateView(CreateView):

    model = ScreenResponse
    template_name = 'ocr/ScreenCreate.html'
    form_class = ScreenForm
    success_url = reverse_lazy('ocr:screen_list')

    # def form_valid(self, form):
    #     self.object = form.save(commit=False)
    #     # perform your action here
    #     self.object.save()
    #     screen = self.object
    #     print(screen.image.path)
    #     blacks = screen.parts.values('black', 'white')
    #     ready_pairs = set((x['black'], x['white']) for x in blacks)
    #     all_values = range(0, 256)
    #     comb = set(itertools.permutations(all_values, 2))
    #     print(f'Распознанных частей для {screen}: {len(ready_pairs)} из {len(comb)}')
    #     unready_pairs = []
    #     for num, pair in enumerate(comb):
    #         if pair in ready_pairs:
    #             continue
    #         unready_pairs.append(pair)
    #         create_response_part.delay(screen.id, black=pair[0], white=pair[1])
    #         # if num >= 1000:
    #         #     break
    #     return super().form_valid(form)


class ScreenListDetail(UpdateView, DetailView):
    model = ScreenResponse
    template_name = 'ocr/ScreenDetail.html'
    fields = '__all__'
    # form_class = ScreenForm
    success_url = reverse_lazy('ocr:screen_list')

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.save()
        screen = self.object
        pairs = screen.parts.values('black', 'white')
        ready_pairs = set((x['black'], x['white']) for x in pairs)
        all_values = range(0, 256)
        comb = list(itertools.permutations(all_values, 2))
        logger.info(f'Распознанных частей для {screen}: {len(ready_pairs)} из {len(comb)}')
        num = 0
        if 'response_button' in self.request.POST:
            empty_pairs = []
            for pair in comb:
                if pair in ready_pairs:
                    continue
                empty_pairs.append(pair)
            logger.info(f'Добавляем в очередь нераспозанных пар: {len(empty_pairs)} шт.')
            # Создание или получение скрина распознавания на удаленном сервере
            image = screen.image.read()
            files = {'image': image}
            logger.info(f'Отправляем запрос {screen.name} {screen.source}')
            REMOTE_CREATE_RESPONSE_ENDPOINT = 'http://45.67.228.39/ocr/create_screen/'
            response = requests.post(REMOTE_CREATE_RESPONSE_ENDPOINT,
                                     data={'name': screen.name, 'source': screen.source},
                                     files=files,
                                     timeout=10)
            logger.info(response.status_code)
            data = response.json()
            logger.info(f'response data: {data}')
            remote_screen_id = data.get('id')
            logger.info(remote_screen_id)
            # add_response_part_to_queue.delay(screen.id, empty_pairs)
            # logger.debug(f'Очередь отправлена')
            # num += 1
            # if num >= 100:
            #     break

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
