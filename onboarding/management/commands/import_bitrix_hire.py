import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, transaction

from onboarding.forms import EmployeeForm
from onboarding.models import Employee, Survey

MAX_SNAPSHOT_BYTES = 16 * 1024


class Command(BaseCommand):
    help = "Локально создаёт карточку и опрос из демонстрационного снимка задачи о приёме."

    def add_arguments(self, parser):
        parser.add_argument("snapshot", type=Path, help="Путь к JSON со снимком одной задачи.")

    def handle(self, *args, **options):
        path = options["snapshot"]
        try:
            with path.open("rb") as source:
                payload = source.read(MAX_SNAPSHOT_BYTES + 1)
            if len(payload) > MAX_SNAPSHOT_BYTES:
                raise CommandError("Размер снимка превышает 16 КБ.")
            task = json.loads(payload.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise CommandError("Не удалось прочитать снимок в формате JSON UTF-8.") from exc
        if not isinstance(task, dict):
            raise CommandError("Снимок должен содержать один объект задачи.")

        task_id = task.get("task_id")
        if type(task_id) is not int or not 1 <= task_id < 2**63:
            raise CommandError("Укажите положительный целый task_id.")
        kind = task.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise CommandError("Укажите явный тип задачи в поле kind.")
        if kind != "hire":
            self.stdout.write(f"Задача №{task_id} пропущена: тип отличается от hire.")
            return

        employee_data = task.get("employee")
        if not isinstance(employee_data, dict) or any(
            not isinstance(employee_data.get(field), str) for field in EmployeeForm.base_fields
        ):
            raise CommandError("Заполните строковые поля employee: имя, подразделение, должность и дату выхода.")
        form = EmployeeForm(employee_data)
        if not form.is_valid():
            fields = ", ".join(form[field].label for field in form.errors)
            raise CommandError(f"Проверьте поля сотрудника: {fields}.")

        try:
            with transaction.atomic():
                employee, created = Employee.objects.get_or_create(
                    bitrix_task_id=task_id, defaults=form.cleaned_data,
                )
                if created:
                    Survey.objects.create(employee=employee)
                elif any(getattr(employee, key) != value for key, value in form.cleaned_data.items()):
                    raise CommandError(f"Задача №{task_id} уже обработана, но данные отличаются. Проверьте карточку вручную.")
        except OperationalError as exc:
            raise CommandError("Не удалось сохранить карточку и опрос. Повторите импорт.") from exc

        if created:
            self.stdout.write(self.style.SUCCESS(f"Созданы карточка №{employee.pk} и опрос из задачи №{task_id}."))
        else:
            self.stdout.write(f"Задача №{task_id} уже обработана. Карточка №{employee.pk}.")
