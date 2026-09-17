"""Basic validators for user-supplied identifiers."""


def validate_username(name):
    if not name:
        raise ValueError("username is empty")
    if len(name) > 32:
        raise ValueError("username too long")
    if not name.isalnum():
        raise ValueError("username must be alphanumeric")
    return True


def validate_slug(slug):
    if not slug:
        raise ValueError("slug is empty")
    if len(slug) > 64:
        raise ValueError("slug too long")
    cleaned = slug.replace("-", "").replace("_", "")
    if not cleaned.isalnum():
        raise ValueError("slug has invalid characters")
    return True


def validate_tag(tag):
    if not tag:
        raise ValueError("tag is empty")
    if len(tag) > 16:
        raise ValueError("tag too long")
    if not tag.isalnum():
        raise ValueError("tag must be alphanumeric")
    return True
