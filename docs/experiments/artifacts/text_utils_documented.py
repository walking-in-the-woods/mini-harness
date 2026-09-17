"""Text processing utilities."""

import re


def clean_whitespace(text, collapse=True):
    """
    Removes extra whitespace from the input text and optionally collapses it into a single space.

    Args:
        text (str): The input text to process.
        collapse (bool): If True, collapses multiple spaces into a single space. Defaults to True.

    Returns:
        str: The cleaned text with collapsed whitespace.
    """
    if collapse:
        return re.sub(r"\s+", " ", text).strip()
    return text.strip()


def word_count(text, min_length=1):
    """
    Counts the number of words in the input text that have a length greater than or equal to min_length.

    Args:
        text (str): The input text to process.
        min_length (int): The minimum length of words to count. Defaults to 1.

    Returns:
        int: The number of words meeting the criteria.
    """
    words = text.split()
    return len([w for w in words if len(w) >= min_length])


def truncate(text, max_length, suffix="..."):
    """
    Truncates the input text to a maximum length and appends a suffix if truncated.

    Args:
        text (str): The input text to process.
        max_length (int): The maximum length of the output text. Must be non-negative.
        suffix (str): The suffix to append if truncation occurs. Defaults to "...".

    Returns:
        str: The truncated or original text with a suffix if necessary.

    Raises:
        ValueError: If max_length is negative.
    """
    if max_length < 0:
        raise ValueError("max_length must be non-negative")
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def extract_emails(text):
    """
    Extracts email addresses from the input text using a regular expression pattern.

    Args:
        text (str): The input text to process.

    Returns:
        list: A list of extracted email addresses.
    """
    pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    return re.findall(pattern, text)