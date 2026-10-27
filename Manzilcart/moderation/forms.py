"""Moderation decision form used by the listing review screen."""

from __future__ import annotations

from django import forms


class ModerationDecisionForm(forms.Form):
    """
    Approve, reject, request changes or suspend a moderated item.

    Rejection / changes-requested / suspension require a reason because the
    seller sees it: a bare "no" is not actionable and generates support work.
    """

    decision = forms.ChoiceField(
        choices=(
            ("approve", "Approve and publish"),
            ("request_changes", "Request changes"),
            ("reject", "Reject"),
            ("suspend", "Suspend (delist immediately)"),
        ),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        required=False,
        max_length=1000,
        label="Reason (required for changes-requested, rejection and suspension)",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "vLargeTextField",
                "placeholder": "Explain the decision. This is shown to the seller.",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get("decision")
        reason = (cleaned.get("reason") or "").strip()
        if decision in {"reject", "request_changes", "suspend"} and not reason:
            label = {
                "reject": "the listing was rejected",
                "request_changes": "changes were requested",
                "suspend": "the item was suspended",
            }[decision]
            self.add_error("reason", f"Please explain why {label}.")
        return cleaned
