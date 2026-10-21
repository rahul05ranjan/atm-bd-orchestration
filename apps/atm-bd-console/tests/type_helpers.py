"""Type assertions for the concrete renderables exercised by UI tests."""

from rich.text import Text
from textual.content import Content


def text_of(value: object) -> Text | Content:
    assert isinstance(value, (Text, Content))
    return value


def rich_text_of(value: object) -> Text:
    assert isinstance(value, Text)
    return value
