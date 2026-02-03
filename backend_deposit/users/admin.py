from django.contrib.auth import get_user_model

from users.models import Profile, Options

User = get_user_model()

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField


class UserCreationForm(forms.ModelForm):
    """A form for creating new users. Includes all the required
    fields, plus a repeated password."""
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Password confirmation', widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ('username', 'email')
        # fields = '__all__'
        list_display_links = ('id', 'username')

    def clean_password2(self):
        # Check that the two password entries match
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords don't match")
        return password2

    def save(self, commit=True):
        # Save the provided password in hashed format
        user = super(UserCreationForm, self).save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        from users.models import Profile
        if commit:
            user.save()
            # Profile.objects.create(
            #     user=user,
            #     email=user.email,
            #     my_filter=[]
            # )
        return user


class UserChangeForm(forms.ModelForm):
    """A form for updating users. Includes all the fields on
    the user, but replaces the password field with admin's
    password hash display field.
    """
    password = ReadOnlyPasswordHashField()

    class Meta:
        model = User
        # fields = ('email', 'password', 'is_superuser')
        fields = '__all__'

    def clean_password(self):
        # Regardless of what the user provides, return the initial value.
        # This is done here, rather than on the field, because the
        # field does not have access to the initial value
        return self.initial["password"]


class ProfileInline(admin.StackedInline):
    model = Profile
    fields = ('first_name', 'last_name', 'view_bad_warning')
    can_delete = False


class UserAdmin(BaseUserAdmin):
    # The forms to add and change user instances
    form = UserChangeForm
    add_form = UserCreationForm

    # The fields to be used in displaying the User model.
    # These override the definitions on the base UserAdmin
    # that reference specific fields on auth.User.
    list_display = ('id', 'username', 'email', 'role', 'is_active', 'is_superuser', 'is_staff', 'group', 'bad_warning')
    list_filter = ('is_superuser', 'groups')
    fieldsets = (
        (None, {'fields': ('username', 'email', 'password')}),
        ('Personal info', {'fields': ('role',)}),
        ('Permissions', {'fields': ('is_staff', 'is_active', "groups", "user_permissions",)}),
    )
    # add_fieldsets is not a standard ModelAdmin attribute. UserAdmin
    # overrides get_fieldsets to use this attribute when creating a user.
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username',  'email', 'password1', 'password2')}
        ),
    )
    search_fields = ('email',)
    ordering = ('email',)
    filter_horizontal = ()
    list_display_links = ('id', 'username')
    inlines = [ProfileInline]


class ProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'first_name', 'last_name', 'view_bad_warning')
    list_display_links = ('id', 'user')


class OptionsAdmin(admin.ModelAdmin):
    list_display = ('id', 'birpay_check', 'card_monitoring_minutes')
    fieldsets = (
        ('Основные настройки', {
            'fields': ('birpay_check', 'card_monitoring_minutes')
        }),
        ('UM (Unified Merchant)', {
            'fields': ('um_login', 'um_password')
        }),
        ('ASU', {
            'fields': ('asu_login', 'asu_password', 'asu_merchant_id', 'asu_secret')
        }),
        ('ASU BirpayShop', {
            'fields': ('asu_birshop_login', 'asu_birshop_password', 'asu_birshop_merchant_id')
        }),
        ('Z-ASU (логика Z-ASU)', {
            'fields': ('z_asu_login', 'z_asu_password'),
            'description': 'Логика Z-ASU: учетные данные для взаимодействия с Payment проектом'
        }),
        ('Birpay API (реквизиты и др.)', {
            'fields': ('birpay_host', 'birpay_login', 'birpay_password'),
            'description': 'Хост и учётные данные для Birpay API (реквизиты Zajon и др.). Пусто — из BIRPAY_HOST, BIRPAY_LOGIN, BIRPAY_PASSWORD'
        }),
        ('GPT проверка чеков', {
            'fields': ('gpt_chek_is_active', 'gpt_auto_approve')
        }),
        ('Списки', {
            'fields': ('birpay_moshennik_list', 'birpay_painter_list')
        }),
    )



admin.site.register(User, UserAdmin)
admin.site.register(Profile, ProfileAdmin)
admin.site.register(Options, OptionsAdmin)
