from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.core.signing import BadSignature, Signer
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import ANSWER_CHOICES, ASSIGNEE_GROUP, HR_GROUP, QUESTIONS, AssignForm, CloseForm, EmployeeForm, SurveyForm
from .models import Employee, Issue, IssueEvent, Survey, survey_deadline


def is_hr(user):
    return user.groups.filter(name=HR_GROUP).exists()


def scoped_issues(user, hr):
    issues = Issue.objects.select_related("survey__employee", "assignee")
    if hr:
        return issues
    if not user.groups.filter(name=ASSIGNEE_GROUP).exists():
        raise PermissionDenied
    return issues.filter(assignee=user, survey__manager_involved=False)


def unavailable(request, status=503):
    title, message = {
        503: ("Не удалось сохранить изменения", "Данные не изменились. Попробуйте ещё раз."),
        409: ("Обращение уже изменилось", "Вернитесь к обращению и обновите страницу."),
    }[status]
    return render(request, "onboarding/notice.html", {"title": title, "message": message}, status=status)


@login_required
@never_cache
def home(request):
    hr = is_hr(request.user)
    all_issues = scoped_issues(request.user, hr)
    available_statuses = Issue.Status.choices if hr else Issue.Status.choices[1:]
    status = request.GET.get("status", "")
    if status not in dict(available_statuses):
        status = ""
    stats = [{
        "code": code, "label": label, "count": all_issues.filter(status=code).count(),
    } for code, label in available_statuses]
    issues = all_issues.filter(status=status) if status else all_issues
    return render(request, "onboarding/home.html", {
        "hr": hr, "issues": issues, "status": status, "stats": stats,
        "employees": Employee.objects.select_related("survey").order_by("-pk") if hr else [],
    })


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def employee_new(request):
    if not is_hr(request.user):
        raise PermissionDenied
    form = EmployeeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                employee = form.save()
                Survey.objects.create(employee=employee)
        except OperationalError:
            return unavailable(request)
        return redirect("employee", pk=employee.pk)
    return render(request, "onboarding/employee_new.html", {"form": form})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def employee_detail(request, pk):
    if not is_hr(request.user):
        raise PermissionDenied
    employee = get_object_or_404(Employee.objects.select_related("survey"), pk=pk)
    survey = employee.survey
    if request.method == "POST":
        try:
            Survey.objects.filter(pk=survey.pk, submitted_at=None, expires_at__lte=timezone.now()).update(
                key=uuid4(), expires_at=survey_deadline(),
            )
        except OperationalError:
            return unavailable(request)
        return redirect("employee", pk=pk)
    survey_url = ""
    if not survey.submitted_at and survey.expires_at > timezone.now():
        token = Signer(salt="pulse.survey").sign(str(survey.key))
        survey_url = request.build_absolute_uri(reverse("survey", args=[token]))
    return render(request, "onboarding/employee.html", {
        "employee": employee, "survey": survey, "survey_url": survey_url,
        "answers": answer_rows(survey),
    })


def answer_rows(survey):
    labels = dict(ANSWER_CHOICES)
    return [(label, labels.get(survey.answers.get(key), "")) for key, (label, _) in QUESTIONS.items()]


@never_cache
@require_http_methods(["GET", "POST"])
def survey_response(request, token):
    try:
        key = Signer(salt="pulse.survey").unsign(token)
        survey = Survey.objects.get(key=key)
    except (BadSignature, ValidationError, Survey.DoesNotExist):
        return render(request, "onboarding/notice.html", {
            "title": "Ссылка недействительна", "message": "Попросите HR прислать актуальную ссылку.",
            "public": True,
        }, status=404)

    if survey.submitted_at:
        return redirect("thanks")
    if survey.expires_at <= timezone.now():
        return render(request, "onboarding/notice.html", {
            "title": "Срок действия ссылки истёк", "message": "Обратитесь в HR.",
            "public": True,
        }, status=410)

    form = SurveyForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        now = timezone.now()
        categories = form.categories()
        try:
            with transaction.atomic():
                updated = Survey.objects.filter(pk=survey.pk, key=survey.key, submitted_at=None, expires_at__gt=now).update(
                    submitted_at=now, answers={key: data[key] for key in QUESTIONS},
                    comment=data["comment"], contact_hr=data["contact_hr"], manager_involved=data["manager_involved"],
                )
                if updated and categories:
                    Issue.objects.create(survey=survey, categories=categories)
        except OperationalError:
            return unavailable(request)
        if not updated and not Survey.objects.filter(pk=survey.pk, submitted_at__isnull=False).exists():
            return render(request, "onboarding/notice.html", {
                "title": "Срок действия ссылки истёк", "message": "Обратитесь в HR.", "public": True,
            }, status=410)
        return redirect("thanks")
    return render(request, "onboarding/survey.html", {"form": form, "public": True})


@never_cache
def thanks(request):
    return render(request, "onboarding/notice.html", {
        "title": "Спасибо за ответы", "message": "Ваши ответы сохранены. Эту вкладку можно закрыть.",
        "public": True, "success": True,
    })


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def issue_detail(request, pk):
    hr = is_hr(request.user)
    issue = get_object_or_404(scoped_issues(request.user, hr), pk=pk)
    assign_form = close_form = None
    can_assign = hr and issue.status == Issue.Status.NEW
    can_close = issue.assignee_id == request.user.pk and issue.status == Issue.Status.ASSIGNED

    if request.method == "POST":
        operation = request.POST.get("operation")
        if operation == "assign":
            if not hr:
                raise PermissionDenied
            if not can_assign:
                return unavailable(request, 409)
        elif operation == "close":
            if issue.assignee_id != request.user.pk:
                raise PermissionDenied
            if not can_close:
                return unavailable(request, 409)
        else:
            return HttpResponseBadRequest("Неизвестное действие.")

        if operation == "assign":
            assign_form = AssignForm(request.POST, actor=request.user, hr_only=issue.survey.manager_involved)
            if assign_form.is_valid():
                assignee = assign_form.cleaned_data["assignee"]
                instruction = assign_form.cleaned_data["instruction"]
                try:
                    with transaction.atomic():
                        updated = Issue.objects.filter(pk=pk, status=Issue.Status.NEW).update(
                            assignee=assignee, instruction=instruction, status=Issue.Status.ASSIGNED,
                        )
                        if updated:
                            IssueEvent.objects.create(issue=issue, actor=request.user, action=IssueEvent.Action.ASSIGNED,
                                                      detail=assignee.get_full_name() or assignee.username)
                except OperationalError:
                    return unavailable(request)
                return redirect("issue", pk=pk) if updated else unavailable(request, 409)
        else:
            close_form = CloseForm(request.POST)
            if close_form.is_valid():
                result = close_form.cleaned_data["result"]
                try:
                    with transaction.atomic():
                        updated = Issue.objects.filter(
                            pk=pk, assignee=request.user, status=Issue.Status.ASSIGNED,
                        ).update(result=result, status=Issue.Status.CLOSED)
                        if updated:
                            IssueEvent.objects.create(issue=issue, actor=request.user,
                                                      action=IssueEvent.Action.CLOSED, detail=result)
                except OperationalError:
                    return unavailable(request)
                return redirect("issue", pk=pk) if updated else unavailable(request, 409)

    if can_assign and assign_form is None:
        assign_form = AssignForm(actor=request.user, hr_only=issue.survey.manager_involved)
    if can_close and close_form is None:
        close_form = CloseForm()
    return render(request, "onboarding/issue.html", {
        "issue": issue, "hr": hr, "assign_form": assign_form, "close_form": close_form,
        "answers": answer_rows(issue.survey) if hr else [],
    })
