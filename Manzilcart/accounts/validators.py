"""Password validators shared by signup, settings and Django Admin."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class ComplexityPasswordValidator:
    """Require upper-case, numeric and special characters in member passwords."""

    def validate(self, password: str, user=None) -> None:
        if not re.search(r"[A-Z]", password):
            raise ValidationError(
                _("Password must contain at least one uppercase letter."),
                code="password_no_uppercase",
            )
        if not re.search(r"[0-9]", password):
            raise ValidationError(
                _("Password must contain at least one digit."),
                code="password_no_digit",
            )
        if not re.search(r"[^A-Za-z0-9\s]", password):
            raise ValidationError(
                _("Password must contain at least one special character."),
                code="password_no_special",
            )

    def get_help_text(self) -> str:
        return _(
            "Your password must be at least 8 characters and include at least one "
            "uppercase letter, one digit and one special character."
        )
