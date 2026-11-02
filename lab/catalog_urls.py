from django.urls import path

from . import catalog_views as views
from .status_views import lot_status

urlpatterns = [
    path('material-categories/', views.category_list, name='category_list'),
    path('material-categories/new/', views.category_save, name='category_create'),
    path('material-categories/<int:pk>/', views.category_detail, name='category_detail'),
    path('material-categories/<int:pk>/edit/', views.category_save, name='category_edit'),
    path('materials/', views.material_list, name='material_list'),
    path('materials/new/', views.material_save, name='material_create'),
    path('materials/<int:pk>/', views.material_detail, name='material_detail'),
    path('materials/<int:pk>/edit/', views.material_save, name='material_edit'),
    path('lots/', views.lot_list, name='lot_list'),
    path('lots/new/', views.lot_save, name='lot_create'),
    path('lots/<int:pk>/', views.lot_detail, name='lot_detail'),
    path('lots/<int:pk>/edit/', views.lot_save, name='lot_edit'),
    path('lots/<int:pk>/status/', lot_status, name='lot_status'),
    path('sources/', views.source_list, name='source_list'),
    path('sources/', views.source_list, name='sources'),
    path('sources/new/', views.source_save, name='source_create'),
    path('sources/<int:pk>/', views.source_detail, name='source_detail'),
    path('sources/<int:pk>/edit/', views.source_save, name='source_edit'),
    path('sources/<int:pk>/download/', views.source_download, name='source_download'),
    path('sources/<int:pk>/sheets/new/', views.source_sheet_create, name='source_sheet_create'),
    path('sources/<int:pk>/sheets/<int:sheet_pk>/cells/new/', views.source_cell_create, name='source_cell_create'),
    path('metrics/', views.metric_list, name='metric_list'),
    path('metrics/new/', views.metric_save, name='metric_create'),
    path('metrics/<int:pk>/edit/', views.metric_save, name='metric_edit'),
    path('mixtures/', views.mixture_list, name='mixture_list'),
    path('mixtures/new/', views.mixture_save, name='mixture_create'),
    path('mixtures/<int:pk>/', views.mixture_detail, name='mixture_detail'),
    path('mixtures/<int:pk>/edit/', views.mixture_save, name='mixture_edit'),
]
