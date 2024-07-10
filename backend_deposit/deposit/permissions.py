from django.contrib.auth.mixins import AccessMixin
from django.shortcuts import redirect


class SuperuserOnlyPerm(AccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('payment:menu')
        else:
            if not request.user.is_superuser:
                # return self.handle_no_permission()
                return redirect('payment:menu')
        return super().dispatch(request, *args, **kwargs)