from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth.models import Group, User
from django.db import OperationalError
from django.test import Client, TestCase
from django.utils import timezone


class WorkflowTests(TestCase):
    def setUp(self):
        hr_group = Group.objects.create(name="HR")
        assignee_group = Group.objects.create(name="Ответственные")
        self.hr = User.objects.create_user("hr", password="test-password")
        self.assignee = User.objects.create_user("it")
        self.other = User.objects.create_user("other")
        self.hr.groups.add(hr_group)
        self.assignee.groups.add(assignee_group)
        self.other.groups.add(assignee_group)
        self.client.force_login(self.hr)
        self.employee = {
            "name": "Марина Ветрова", "department": "Склад",
            "position": "Кладовщик", "start_date": date.today().isoformat(),
        }
        self.answers = {"duties": "yes", "tools": "no", "support": "yes", "comment": "Личный комментарий"}

    def create_survey(self):
        response = self.client.post("/employees/new/", self.employee)
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertEqual(page.status_code, 200)
        return urlsplit(page.context["survey_url"]).path

    def test_problem_creates_one_case_and_retry_keeps_original(self):
        from .models import Issue, Survey

        url = self.create_survey()
        person = Client()
        self.assertEqual(person.get(url).status_code, 200)
        self.assertEqual(person.post(url, self.answers).status_code, 302)
        self.assertEqual(person.post(url, {**self.answers, "comment": "Замена"}).status_code, 302)
        self.assertEqual(Issue.objects.count(), 1)
        self.assertEqual(Issue.objects.get().categories, ["Инструменты и доступы"])
        self.assertEqual(Survey.objects.get().comment, "Личный комментарий")

    def test_positive_answers_are_saved_without_a_case(self):
        from .models import Issue, Survey

        response = Client().post(self.create_survey(), {**self.answers, "tools": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Issue.objects.count(), 0)
        self.assertIsNotNone(Survey.objects.get().submitted_at)

    def test_employee_can_request_hr_with_positive_answers(self):
        from .models import Issue

        response = Client().post(self.create_survey(), {**self.answers, "tools": "yes", "contact_hr": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Issue.objects.get().categories, ["Просьба связаться с HR"])

    def test_unknown_and_expired_links_are_rejected(self):
        from .models import Issue, Survey

        url = self.create_survey()
        Survey.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        person = Client()
        self.assertEqual(person.get("/survey/not-a-real-token/").status_code, 404)
        self.assertEqual(person.post(url, self.answers).status_code, 410)
        self.assertFalse(Issue.objects.exists())

    def test_failed_write_rolls_back_survey_without_success_confirmation(self):
        from .models import Issue, Survey

        url = self.create_survey()
        with patch("onboarding.views.Issue.objects.create", side_effect=OperationalError("write failed")):
            self.assertEqual(Client().post(url, self.answers).status_code, 503)
        self.assertIsNone(Survey.objects.get().submitted_at)
        self.assertFalse(Issue.objects.exists())
        self.assertEqual(Client().post(url, self.answers).status_code, 302)

    def test_manager_related_issue_stays_with_hr(self):
        from .models import Issue

        Client().post(self.create_survey(), {**self.answers, "tools": "yes", "manager_involved": "on"})
        case = Issue.objects.get()
        url = f"/issues/{case.pk}/"
        payload = {"operation": "assign", "assignee": self.assignee.pk, "instruction": "Обсудить ситуацию"}
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        case.refresh_from_db()
        self.assertIsNone(case.assignee)
        self.assertEqual(self.client.post(url, {**payload, "assignee": self.hr.pk}).status_code, 302)
        worker = Client()
        worker.force_login(self.assignee)
        self.assertEqual(worker.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"operation": "close", "result": "HR обсудил ситуацию"}).status_code, 302)

    def test_survey_rejects_submission_without_csrf_token(self):
        person = Client(enforce_csrf_checks=True)
        self.assertEqual(person.post(self.create_survey(), self.answers).status_code, 403)

    def test_assignment_access_and_closure(self):
        from .models import Issue, IssueEvent

        Client().post(self.create_survey(), self.answers)
        case = Issue.objects.get()
        url = f"/issues/{case.pk}/"
        self.assertContains(self.client.get(url), "Личный комментарий")
        self.assertEqual(self.client.post(url, {
            "operation": "assign", "assignee": self.assignee.pk, "instruction": "Выдать доступ к системе",
        }).status_code, 302)

        stranger = Client()
        self.assertEqual(stranger.get(url).status_code, 302)
        stranger.force_login(self.other)
        self.assertEqual(stranger.get(url).status_code, 404)
        self.assertEqual(stranger.post(url, {"operation": "close", "result": "Готово"}).status_code, 404)

        worker = Client()
        worker.force_login(self.assignee)
        page = worker.get(url)
        self.assertContains(page, "Выдать доступ к системе")
        self.assertNotContains(page, "Личный комментарий")
        self.assertEqual(worker.post(url, {"operation": "close", "result": "  "}).status_code, 200)
        case.refresh_from_db()
        self.assertEqual(case.status, Issue.Status.ASSIGNED)
        self.assertEqual(worker.post(url, {"operation": "close", "result": "Доступ выдан и проверен"}).status_code, 302)
        self.assertEqual(worker.post(url, {"operation": "close", "result": "Повтор"}).status_code, 409)
        case.refresh_from_db()
        self.assertEqual(case.status, Issue.Status.CLOSED)
        self.assertEqual(case.result, "Доступ выдан и проверен")
        self.assertEqual(list(IssueEvent.objects.values_list("action", flat=True)), ["assigned", "closed"])
        self.assertContains(self.client.get(url), "Доступ выдан и проверен")
