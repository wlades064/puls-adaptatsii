import json
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Employee, Survey


class BitrixPullTests(TestCase):
    fields = Path(__file__).resolve().parent.parent / "examples" / "bitrix-field-map.json"

    def setUp(self):
        self.url = "https://portal.example/rest/12/test-secret/"
        self.task = {"id": "31", "ufHireKind": "hire", "ufHireName": "Мила Тихая",
                     "ufHireDepartment": "Склад", "ufHirePosition": "Кладовщик", "ufHireStartDate": "2026-11-14"}
        env = patch.dict("os.environ", {"PULSE_BITRIX_WEBHOOK": self.url})
        env.start()
        self.addCleanup(env.stop)
        opener = patch("onboarding.management.commands.pull_bitrix_hire.build_opener")
        self.connection = opener.start().return_value.open
        self.addCleanup(opener.stop)

    def respond(self, document):
        self.connection.side_effect = lambda *args, **kwargs: BytesIO(json.dumps(document).encode())

    def pull(self, apply=False):
        output = StringIO()
        args = ("--apply",) if apply else ()
        call_command("pull_bitrix_hire", "31", "--fields", str(self.fields), *args, stdout=output)
        return output.getvalue()

    def test_default_checks_without_writing_and_only_fetches_mapped_fields(self):
        self.respond({"result": {"task": self.task}})
        self.assertIn("поля проверены", self.pull())
        self.assertFalse(Employee.objects.exists())
        request = self.connection.call_args.args[0]
        self.assertEqual(request.full_url, self.url + "tasks.task.get")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {
            "taskId": 31,
            "select": ["ID", "UF_HIRE_KIND", "UF_HIRE_NAME", "UF_HIRE_DEPARTMENT",
                       "UF_HIRE_POSITION", "UF_HIRE_START_DATE"],
        })

    def test_apply_is_idempotent(self):
        self.respond({"result": {"task": self.task}})
        self.assertIn("созданы", self.pull(apply=True))
        self.assertIn("уже существует", self.pull(apply=True))
        self.assertEqual(Employee.objects.get().bitrix_task_id, 31)
        self.assertEqual(Survey.objects.count(), 1)

    def test_wrong_returned_id_and_missing_access_fail_without_writing(self):
        for document in [{"result": []}, {"result": {"task": {**self.task, "id": 300}}}]:
            self.respond(document)
            with self.assertRaises(CommandError):
                self.pull(apply=True)
        self.assertFalse(Employee.objects.exists())

    def test_skips_wrong_kind_and_rejects_missing_fields(self):
        self.respond({"result": {"task": {**self.task, "ufHireKind": "dismissal"}}})
        self.assertIn("пропущена", self.pull(apply=True))
        for field in ["ufHireKind", "ufHireDepartment"]:
            self.respond({"result": {"task": {**self.task, field: None}}})
            with self.assertRaises(CommandError):
                self.pull(apply=True)
        self.assertFalse(Employee.objects.exists())

    def test_bad_endpoint_and_http_error_do_not_disclose_secret(self):
        with patch.dict("os.environ", {"PULSE_BITRIX_WEBHOOK": self.url.replace("https", "http")}):
            with self.assertRaisesMessage(CommandError, "HTTPS"):
                self.pull()
        self.connection.assert_not_called()
        self.connection.side_effect = HTTPError(self.url, 403, "denied", None, None)
        with self.assertRaises(CommandError) as failure:
            self.pull()
        self.assertIn("403", str(failure.exception))
        self.assertNotIn("test-secret", str(failure.exception))
        self.assertIsNone(failure.exception.__cause__)
