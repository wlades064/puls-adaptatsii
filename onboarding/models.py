from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone


def survey_deadline():
    return timezone.now() + timedelta(days=7)


class Employee(models.Model):
    name = models.CharField("Имя сотрудника", max_length=100)
    department = models.CharField("Подразделение", max_length=50)
    position = models.CharField("Должность", max_length=100)
    start_date = models.DateField("Дата выхода")


class Survey(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="survey")
    key = models.UUIDField(default=uuid4, unique=True, editable=False)
    expires_at = models.DateTimeField(default=survey_deadline)
    submitted_at = models.DateTimeField(null=True, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    comment = models.TextField(blank=True)
    contact_hr = models.BooleanField(default=False)
    manager_involved = models.BooleanField(default=False)


class Issue(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новое"
        ASSIGNED = "assigned", "Назначено"
        CLOSED = "closed", "Закрыто"

    survey = models.OneToOneField(Survey, on_delete=models.CASCADE, related_name="issue")
    categories = models.JSONField()
    status = models.CharField(max_length=10, choices=Status, default=Status.NEW)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    instruction = models.TextField(blank=True)
    result = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class IssueEvent(models.Model):
    class Action(models.TextChoices):
        ASSIGNED = "assigned", "Назначено"
        CLOSED = "closed", "Закрыто"

    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=10, choices=Action)
    detail = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
