import itertools
import logging
import time

from django.conf import settings
from django.db.models import Count, Window, OuterRef, Q, F
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, DetailView, UpdateView

from deposit.tasks import create_response_part, add_response_part_to_queue
from ocr.forms import ScreenForm
from ocr.models import ScreenResponse


logger = logging.getLogger(__name__)


class ScreenListView(ListView):

    model = ScreenResponse
    template_name = 'ocr/ScreenList.html'
    paginate_by = 10
    ordering = ('-id',)


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
    success_url = reverse_lazy('ocr:screen_list')

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.save()
        screen = self.object
        blacks = screen.parts.values('black', 'white')
        ready_pairs = set((x['black'], x['white']) for x in blacks)
        all_values = range(0, 256)
        comb = set(itertools.permutations(all_values, 2))
        logger.info(f'Распознанных частей для {screen}: {len(ready_pairs)} из {len(comb)}')
        num = 0
        if self.request.POST.get('response_button'):
            empty_pairs = []
            for pair in comb:
                if pair in ready_pairs:
                    continue
                empty_pairs.append(pair)
            add_response_part_to_queue.delay(screen.id, empty_pairs)
            # num += 1
            # if num >= 100:
            #     break

        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        print('get_context_data', self, kwargs)
        # screen = ScreenResponse.objects.get(id=55)
        screen = self.object
        print(screen.parts.count())
        senders = screen.parts.values('sender').annotate(count=Count('sender')).order_by('-count')
        transactions = screen.parts.values('transaction').annotate(count=Count('transaction')).order_by('-count')
        recipients = screen.parts.values('recipient').annotate(count=Count('recipient')).order_by('-count')
        response_dates = screen.parts.values('response_date').annotate(count=Count('response_date')).order_by('-count')
        context['transactions'] = transactions
        context['recipients'] = recipients
        context['senders'] = senders
        context['response_dates'] = response_dates
        return context
