"""Moderation decision form used by the listing review screen."""

from __future__ import annotations

from django import forms


class ModerationDecisionForm(forms.Form):
    """
    Approve or reject a listing.

    A rejection requires a reason because the seller sees it: "rejected" with no
    explanation is not actionable and generates support work.
    """

    decision = forms.ChoiceField(
        choices=(("approve", "Approve and publish"), ("reject", "Reject")),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        required=False,
        max_length=1000,
        label="Reason (required when rejecting)",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "vLargeTextField",
                "placeholder": "Explain what needs to change. This is shown to the seller.",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "reject" and not (cleaned.get("reason") or "").strip():
            self.add_error("reason", "Please tell the seller why the listing was rejected.")
        return cleaned
