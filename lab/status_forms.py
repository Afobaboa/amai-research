"""Small forms for changing a record's status without editing its contents."""
from django import forms

from .models import RecipeVersion, TestSession


class StatusForm(forms.Form):
    status = forms.ChoiceField(label='Новый статус')
    current_status = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, instance, **kwargs):
        super().__init__(*args, **kwargs)
        choices = instance.Status.choices
        if isinstance(instance, RecipeVersion):
            choices = [(RecipeVersion.Status.PUBLISHED, 'Опубликована')]
        self.fields['status'].choices = [
            (value, label) for value, label in choices
            if value != instance.status
        ]
        self.initial['current_status'] = instance.status
        self.initial['status'] = self.fields['status'].choices[0][0]
        if isinstance(instance, TestSession):
            self.initial['status'] = (
                TestSession.Status.ACTIVE if instance.status == 'draft'
                else TestSession.Status.COMPLETED
            )
