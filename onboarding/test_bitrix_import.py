import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import TestCase

from .models import Employee, Survey


class BitrixImportTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.snapshot = Path(folder.name) / "task.json"
        self.task = {
            "task_id": 901,
            "kind": "hire",
            "employee": {
                "name": "Дарья Северова",
                "department": "Склад",
                "position": "Кладовщик",
                "start_date": "2026-11-14",
            },
        }

    def import_task(self):
        self.snapshot.write_text(json.dumps(self.task), encoding="utf-8")
        output = StringIO()
        call_command("import_bitrix_hire", str(self.snapshot), stdout=output, no_color=True)
        return output.getvalue()

    def test_hire_creates_employee_and_survey_only_once(self):
        self.assertIn("Созданы", self.import_task())
        employee = Employee.objects.get()
        survey_key = employee.survey.key
        self.assertEqual(employee.bitrix_task_id, 901)
        self.assertEqual(employee.department, "Склад")
        self.assertIn("уже обработана", self.import_task())
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(Survey.objects.get().key, survey_key)

    def test_non_hire_is_skipped_and_missing_marker_is_rejected(self):
        self.task["kind"] = "termination"
        self.assertIn("пропущена", self.import_task())
        self.task.pop("kind")
        with self.assertRaises(CommandError):
            self.import_task()
        self.assertFalse(Employee.objects.exists())

    def test_invalid_employee_or_task_id_creates_nothing(self):
        self.task["employee"]["start_date"] = "14/99/2026"
        with self.assertRaises(CommandError):
            self.import_task()
        self.task["employee"]["start_date"] = "2026-11-14"
        for invalid_id in [True, 0, 2**63, "901"]:
            with self.subTest(invalid_id=invalid_id):
                self.task["task_id"] = invalid_id
                with self.assertRaises(CommandError):
                    self.import_task()
        self.assertFalse(Employee.objects.exists())

    def test_file_is_rejected_if_malformed_or_oversized(self):
        for payload in [b"{", b"[]" + b" " * 16384]:
            with self.subTest(length=len(payload)):
                self.snapshot.write_bytes(payload)
                with self.assertRaises(CommandError):
                    call_command("import_bitrix_hire", str(self.snapshot), stdout=StringIO())
        self.assertFalse(Employee.objects.exists())

    def test_conflicting_replay_preserves_original(self):
        self.import_task()
        self.task["employee"]["position"] = "Консультант"
        with self.assertRaisesMessage(CommandError, "данные отличаются"):
            self.import_task()
        self.assertEqual(Employee.objects.get().position, "Кладовщик")
        self.assertEqual(Survey.objects.count(), 1)

    def test_failed_survey_write_rolls_back_employee_and_allows_retry(self):
        with patch.object(Survey.objects, "create", side_effect=OperationalError):
            with self.assertRaises(CommandError):
                self.import_task()
        self.assertFalse(Employee.objects.exists())
        self.import_task()
        self.assertEqual(Survey.objects.count(), 1)
