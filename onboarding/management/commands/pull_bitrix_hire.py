import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.core.management.base import BaseCommand, CommandError

from onboarding.bitrix_intake import employee_fields, import_hire

FIELDS = ("kind", "name", "department", "position", "start_date")
MAX_RESPONSE = 64 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint():
    raw = os.environ.get("PULSE_BITRIX_WEBHOOK", "").strip()
    try:
        url = urlsplit(raw)
        if (url.scheme == "https" and url.hostname and not url.username and not url.password
                and url.port in (None, 443) and not url.query and not url.fragment
                and re.fullmatch(r"/rest/[1-9][0-9]*/[A-Za-z0-9_-]+/?", url.path)):
            return raw.rstrip("/") + "/tasks.task.get"
    except ValueError:
        pass
    raise CommandError("В PULSE_BITRIX_WEBHOOK нужен базовый HTTPS-адрес /rest/ID/ключ/.")


def read_task(url, task_id, select):
    request = Request(url, data=json.dumps({"taskId": task_id, "select": select}).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise CommandError("Ответ Битрикс24 слишком велик.")
        document = json.loads(payload)
    except HTTPError as error:
        raise CommandError(f"Битрикс24 вернул HTTP {error.code}. Проверьте URL вебхука и права.") from None
    except (URLError, TimeoutError, OSError, ValueError, RecursionError):
        raise CommandError("Не удалось получить ответ Битрикс24. Проверьте сеть и настройки.") from None
    if not isinstance(document, dict) or "error" in document:
        raise CommandError("Битрикс24 отклонил запрос или вернул неожиданный ответ.")
    result = document.get("result")
    task = result.get("task") if isinstance(result, dict) else None
    if not isinstance(task, dict):
        raise CommandError("Задача не найдена или недоступна владельцу вебхука.")
    if str(task.get("id")) != str(task_id):
        raise CommandError("Получен неожиданный ID задачи.")
    return task


def read_mapping(path):
    try:
        with open(path, encoding="utf-8-sig") as source:
            mapping = json.load(source)
        if not isinstance(mapping, dict) or set(mapping) != {*FIELDS, "hire_value"}:
            raise ValueError
        hire_value = mapping.pop("hire_value")
        if not isinstance(hire_value, str) or not hire_value.strip():
            raise ValueError
        codes = [
            vendor_code
            for pair in mapping.values()
            if isinstance(pair, list) and len(pair) == 2
            for vendor_code in pair
        ]
        if len(codes) != 2 * len(FIELDS) or not all(
            isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", code)
            for code in codes
        ):
            raise ValueError
        if (len({mapping[key][0] for key in FIELDS}) != len(FIELDS)
                or len({mapping[key][1] for key in FIELDS}) != len(FIELDS)):
            raise ValueError
    except (OSError, ValueError, RecursionError):
        raise CommandError("Проверьте файл соответствия полей. Пример: examples/bitrix-field-map.json.") from None
    return mapping, hire_value.strip()


class Command(BaseCommand):
    help = "Читает одну задачу Битрикс24. --apply создаёт локальную карточку и опрос."

    def add_arguments(self, parser):
        parser.add_argument("task_id", type=int)
        parser.add_argument("--fields", required=True, help="Путь к файлу соответствия полей.")
        parser.add_argument("--apply", action="store_true", help="Сохранить результат в локальной базе.")

    def handle(self, *args, **options):
        task_id = options["task_id"]
        if not 1 <= task_id < 2**63:
            raise CommandError("Укажите положительный ID задачи.")
        mapping, hire_value = read_mapping(options["fields"])
        task = read_task(endpoint(), task_id, ["ID", *(mapping[key][0] for key in FIELDS)])
        marker = task.get(mapping["kind"][1])
        if not isinstance(marker, str) or not marker.strip():
            raise CommandError("Признак задачи отсутствует. Проверьте соответствие полей и права вебхука.")
        if marker.strip() != hire_value:
            self.stdout.write(f"Задача №{task_id} пропущена: признак приёма не совпал.")
            return
        fields = employee_fields({key: task.get(mapping[key][1]) for key in FIELDS if key != "kind"})
        if not options["apply"]:
            self.stdout.write(f"Задача №{task_id}: признак приёма и поля проверены. Запустите с --apply для импорта.")
            return
        employee, created = import_hire(task_id, fields)
        outcome = "созданы карточка и опрос" if created else "карточка уже существует"
        self.stdout.write(f"Задача №{task_id}: {outcome}. Карточка №{employee.pk}.")
