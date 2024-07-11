from django.contrib.auth.mixins import AccessMixin
from django.shortcuts import redirect


class SuperuserOnlyPerm(AccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('deposit:index')
        else:
            if not request.user.is_superuser:
                # return self.handle_no_permission()
                return redirect('deposit:index')
        return super().dispatch(request, *args, **kwargs)