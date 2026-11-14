from django.contrib.auth import views as auth_views
from django.urls import path

from onboarding import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", auth_views.LoginView.as_view(template_name="onboarding/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("employees/new/", views.employee_new, name="employee_new"),
    path("employees/<int:pk>/", views.employee_detail, name="employee"),
    path("issues/<int:pk>/", views.issue_detail, name="issue"),
    path("survey/thanks/", views.thanks, name="thanks"),
    path("survey/<str:token>/", views.survey_response, name="survey"),
]
