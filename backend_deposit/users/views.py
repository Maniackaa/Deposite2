from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView

from .forms import CreationForm
from .models import Options


class SignUp(CreateView):
    form_class = CreationForm
    success_url = reverse_lazy('users:login')
    template_name = 'users/signup.html'


def toggle_option(request, value):
    opts = Options.load()
    if not hasattr(opts, value):
        raise Http404("Нет такого поля")
    field = getattr(opts, value)
    if not isinstance(field, bool):
        raise Http404("Можно только для булевых опций")
    setattr(opts, value, not field)
    opts.save()
    return redirect(request.META.get('HTTP_REFERER', request.path))