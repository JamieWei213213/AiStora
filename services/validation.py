"""Input validation for user-supplied names.

These names are rendered in the browser and are used by the agent to address
tables. The frontend escapes them on output, but names are also stored, logged
and echoed back through the API, so they are constrained on the way in as well.
"""

import re
import unicodedata


class ValidationError(ValueError):
    """Raised when user input is rejected."""


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_DATABASE_NAME = re.compile(r"^[^\W_][\w \-.,'&()]{0,79}$", re.UNICODE)
_TABLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-. ]{0,63}$")


def _normalise(value, field):
    if value is None:
        raise ValidationError(f"{field} is required.")
    text = unicodedata.normalize("NFC", str(value)).strip()
    text = " ".join(text.split())
    if not text:
        raise ValidationError(f"{field} is required.")
    if _CONTROL_CHARACTERS.search(text):
        raise ValidationError(f"{field} may not contain control characters.")
    if "<" in text or ">" in text:
        raise ValidationError(f"{field} may not contain angle brackets.")
    return text


def validate_database_name(value):
    text = _normalise(value, "Database name")
    if len(text) > 80:
        raise ValidationError("Database name may be at most 80 characters.")
    if not _DATABASE_NAME.match(text):
        raise ValidationError(
            "Database name must start with a letter or digit and may contain "
            "letters, digits, spaces and - . , ' & ( ) _ only."
        )
    return text


def validate_table_name(value):
    text = _normalise(value, "Table name")
    if len(text) > 64:
        raise ValidationError("Table name may be at most 64 characters.")
    if not _TABLE_NAME.match(text):
        raise ValidationError(
            "Table name must start with a letter or digit and may contain "
            "letters, digits, spaces, underscores, hyphens and dots only."
        )
    return text


# RFC 5322 in full is not worth implementing; this rejects the obviously
# malformed while accepting anything a real mail server would route.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

MAX_EMAIL_LENGTH = 120
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256


def validate_email(value):
    text = _normalise(value, "Email address").casefold()
    if len(text) > MAX_EMAIL_LENGTH:
        raise ValidationError(
            f"Email address may be at most {MAX_EMAIL_LENGTH} characters."
        )
    if not _EMAIL.match(text):
        raise ValidationError("Enter a valid email address.")
    return text


def validate_password(value):
    if value is None:
        raise ValidationError("Password is required.")
    password = str(value)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
        )
    if password.strip() == "":
        raise ValidationError("Password may not be only whitespace.")
    return password


def validate_column_names(columns, max_columns=200, max_name_chars=64):
    """Reject CSV headers that would inflate every model prompt.

    Column names are sent to the model on each turn in the default privacy
    mode, so a header with thousands of columns, or with names thousands of
    characters long, turns one upload into an open-ended token bill. Control
    characters are rejected because names are rendered and logged.
    """
    names = list(columns)
    if max_columns and len(names) > max_columns:
        raise ValidationError(
            f"CSV files may have at most {max_columns} columns "
            f"(this file has {len(names)})."
        )
    for name in names:
        text = str(name)
        if max_name_chars and len(text) > max_name_chars:
            raise ValidationError(
                f"Column names may be at most {max_name_chars} characters: "
                f"'{text[:24]}...'."
            )
        if _CONTROL_CHARACTERS.search(text):
            raise ValidationError(
                "Column names may not contain control characters."
            )
    return names
