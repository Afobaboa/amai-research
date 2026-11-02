"""Feedback available to every signed-in laboratory employee."""

from django import forms

from .models import Feedback


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ["text"]
        widgets = {
            "text": forms.Textarea(attrs={
                "rows": 8,
                "autofocus": True,
                "placeholder": "Опишите проблему или предложите улучшение",
            }),
        }
        help_texts = {"text": "До 5000 символов."}

