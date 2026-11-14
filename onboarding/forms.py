from django import forms
from django.contrib.auth.models import User

from .models import Employee

HR_GROUP = "HR"
ASSIGNEE_GROUP = "Ответственные"
DEPARTMENTS = ("Склад", "Магазины", "Отдел продаж", "Офис")
QUESTIONS = {
    "duties": ("Вам понятно, что входит в ваши обязанности?", "Обязанности"),
    "tools": ("У вас есть всё, чтобы выполнять работу?", "Инструменты и доступы"),
    "support": ("Вы получаете помощь, когда она нужна?", "Поддержка"),
}
ANSWER_CHOICES = (("yes", "Да"), ("partial", "Частично"), ("no", "Нет"))


class EmployeeForm(forms.ModelForm):
    department = forms.ChoiceField(label="Подразделение", choices=[(item, item) for item in DEPARTMENTS])

    class Meta:
        model = Employee
        fields = ("name", "department", "position", "start_date")
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class SurveyForm(forms.Form):
    duties = forms.ChoiceField(label=QUESTIONS["duties"][0], choices=ANSWER_CHOICES, widget=forms.RadioSelect)
    tools = forms.ChoiceField(label=QUESTIONS["tools"][0], choices=ANSWER_CHOICES, widget=forms.RadioSelect)
    support = forms.ChoiceField(label=QUESTIONS["support"][0], choices=ANSWER_CHOICES, widget=forms.RadioSelect)
    comment = forms.CharField(label="Что ещё важно сообщить HR?", max_length=1000, required=False,
                              widget=forms.Textarea(attrs={"rows": 3}))
    contact_hr = forms.BooleanField(label="Хочу, чтобы со мной связался HR", required=False)
    manager_involved = forms.BooleanField(label="Вопрос касается моего руководителя", required=False)

    def categories(self):
        categories = [category for key, (_, category) in QUESTIONS.items() if self.cleaned_data[key] != "yes"]
        if self.cleaned_data["contact_hr"]:
            categories.append("Просьба связаться с HR")
        if self.cleaned_data["manager_involved"]:
            categories.append("Вопрос о руководителе")
        return categories


class AssigneeField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return user.get_full_name() or user.username


class AssignForm(forms.Form):
    assignee = AssigneeField(label="Ответственный", queryset=User.objects.none())
    instruction = forms.CharField(label="Что нужно сделать?", max_length=1000,
                                  widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, actor, hr_only, **kwargs):
        super().__init__(*args, **kwargs)
        eligible = User.objects.filter(is_active=True)
        if hr_only:
            eligible = eligible.filter(pk=actor.pk)
        else:
            eligible = eligible.filter(groups__name=ASSIGNEE_GROUP)
        self.fields["assignee"].queryset = eligible.order_by("first_name", "username")


class CloseForm(forms.Form):
    result = forms.CharField(label="Действие и результат", max_length=1000,
                             widget=forms.Textarea(attrs={"rows": 4}))
