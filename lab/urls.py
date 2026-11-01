from django.urls import include, path

from . import views

urlpatterns = [
    path('', include('lab.catalog_urls')),
    path('', include('lab.template_urls')),
    path("login/", views.login_view, name="login"),
    path("employees/", views.choose_employee, name="choose_employee"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("recipes/", views.recipe_list, name="recipe_list"),
    path("recipes/new/", views.recipe_create, name="recipe_create"),
    path("recipes/<int:pk>/", views.recipe_detail, name="recipe_detail"),
    path("recipes/<int:pk>/edit/", views.recipe_create, name="recipe_edit"),
    path('recipes/<int:pk>/calculate/', views.recipe_calculate, name='recipe_calculate'),
    path('calculations/<int:pk>/', views.calculation_detail, name='calculation_detail'),
    path("recipes/<int:source_pk>/fork/", views.recipe_create, name="recipe_fork"),
    path("tests/", views.test_list, name="test_list"),
    path("tests/new/", views.test_create, name="test_create"),
    path("tests/<int:pk>/", views.test_detail, name="test_detail"),
    path("tests/<int:pk>/edit/", views.test_create, name="test_edit"),
    path("tests/<int:pk>/results/new/", views.result_add, name="result_add"),
    path("tests/<int:pk>/observations/new/", views.observation_add, name="observation_add"),
    path("tests/<int:pk>/conditioning/new/", views.conditioning_add, name="conditioning_add"),
    path("history/", views.history, name="history"),
    path("sources/", views.sources, name="sources"),
]
