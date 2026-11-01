from django.urls import path

from . import template_views as views

urlpatterns = [
    path('recipe-templates/', views.recipe_template_list, name='recipe_template_list'),
    path('recipe-templates/new/', views.recipe_template_create, name='recipe_template_create'),
    path('recipe-templates/from-recipe/<int:recipe_pk>/', views.recipe_template_create, name='recipe_template_from_recipe'),
    path('recipe-templates/<int:pk>/', views.recipe_template_detail, name='recipe_template_detail'),
    path('recipe-templates/<int:source_pk>/fork/', views.recipe_template_create, name='recipe_template_fork'),
    path('calculation-templates/', views.calculation_template_list, name='calculation_template_list'),
    path('calculation-templates/new/', views.calculation_template_create, name='calculation_template_create'),
    path('calculation-templates/<int:pk>/', views.calculation_template_detail, name='calculation_template_detail'),
    path('calculation-templates/<int:source_pk>/fork/', views.calculation_template_create, name='calculation_template_fork'),
]
