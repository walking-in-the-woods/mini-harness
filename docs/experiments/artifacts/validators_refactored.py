"""Basic validators for user-supplied identifiers."""


def _validate_basic(name, max_length, error_message):
    if not name:
        raise ValueError(f"{name} is empty")
    if len(name) > max_length:
        raise ValueError(f"{name} too long")
    cleaned = name.replace("-", "").replace("_", "")
    if not cleaned.isalnum():
        raise ValueError(error_message)


def validate_username(name):
    _validate_basic(name, 32, "username must be alphanumeric")


def validate_slug(slug):
    _validate_basic(slug, 64, "slug has invalid characters")


def validate_tag(tag):
    _validate_basic(tag, 16, "tag must be alphanumeric")
