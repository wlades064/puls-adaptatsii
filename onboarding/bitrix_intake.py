from django.core.management.base import CommandError
from django.db import OperationalError, transaction

from .forms import EmployeeForm
from .models import Employee, Survey


def employee_fields(data):
    if not isinstance(data, dict) or any(
        not isinstance(data.get(field), str) for field in EmployeeForm.base_fields
    ):
        raise CommandError("Заполните строковые поля сотрудника: имя, подразделение, должность и дату выхода.")
    form = EmployeeForm(data)
    if not form.is_valid():
        fields = ", ".join(form[field].label for field in form.errors)
        raise CommandError(f"Проверьте поля сотрудника: {fields}.")
    return form.cleaned_data


def import_hire(task_id, fields):
    try:
        with transaction.atomic():
            employee, created = Employee.objects.get_or_create(bitrix_task_id=task_id, defaults=fields)
            if created:
                Survey.objects.create(employee=employee)
            elif any(getattr(employee, key) != value for key, value in fields.items()):
                raise CommandError(f"Задача №{task_id} уже обработана, но данные отличаются. Проверьте карточку вручную.")
    except OperationalError:
        raise CommandError("Не удалось сохранить карточку и опрос. Повторите импорт.") from None
    return employee, created
