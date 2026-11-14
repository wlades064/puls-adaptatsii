import os
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from onboarding.forms import ASSIGNEE_GROUP, HR_GROUP
from onboarding.models import Employee, Survey


class Command(BaseCommand):
    help = "Создаёт демонстрационные учётные записи и вымышленных сотрудников."

    @transaction.atomic
    def handle(self, *args, **options):
        password = os.environ.get("PULSE_DEMO_PASSWORD")
        if not password or len(password) < 12:
            raise CommandError("Укажите PULSE_DEMO_PASSWORD длиной от 12 символов.")
        hr, _ = Group.objects.get_or_create(name=HR_GROUP)
        assignees, _ = Group.objects.get_or_create(name=ASSIGNEE_GROUP)
        for username, first_name, last_name, group in [
            ("demo-hr", "Полина", "Лаврова", hr),
            ("demo-it", "Артём", "Морозов", assignees),
            ("demo-ops", "Ирина", "Зорина", assignees),
        ]:
            user, _ = User.objects.get_or_create(username=username)
            user.first_name = first_name
            user.last_name = last_name
            user.set_password(password)
            user.save()
            user.groups.set([group])

        today = timezone.localdate()
        if not Employee.objects.exists():
            for name, department, position, offset in [
                ("Михаил Чижов", "Офис", "Специалист поддержки", 2),
                ("Алина Вересова", "Магазины", "Консультант", 6),
                ("Илья Романов", "Склад", "Кладовщик", 7),
            ]:
                employee = Employee.objects.create(
                    name=name, department=department, position=position,
                    start_date=today - timedelta(days=offset),
                )
                Survey.objects.create(employee=employee)
        self.stdout.write(self.style.SUCCESS("Демонстрационные данные готовы."))
