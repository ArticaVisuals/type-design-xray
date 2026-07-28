"""Tolerant reader for the OpenStep-style property lists used by Glyphs."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Union


class PlistParseError(ValueError):
    """Raised when an OpenStep property list cannot be parsed."""


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text.lstrip("\ufeff")
        self.index = 0
        self.length = len(self.text)

    def parse(self) -> Dict[str, Any]:
        self._skip_trivia()
        if self._peek_raw() == "{":
            value = self._parse_dict()
        else:
            value = self._parse_unbraced_dict()
        self._skip_trivia()
        while self._peek_raw() in (";", ","):
            self.index += 1
            self._skip_trivia()
        if self.index != self.length:
            self._fail("unexpected content after the root object")
        return value

    def _parse_value(self) -> Any:
        self._skip_trivia()
        char = self._peek_raw()
        if char == "{":
            return self._parse_dict()
        if char == "(":
            return self._parse_array()
        if char == '"':
            return self._parse_quoted()
        if not char:
            self._fail("expected a value")
        return self._parse_bare()

    def _parse_dict(self) -> Dict[str, Any]:
        self._expect("{")
        result: Dict[str, Any] = {}
        while True:
            self._skip_trivia()
            char = self._peek_raw()
            if char == "}":
                self.index += 1
                return result
            if not char:
                self._fail("unterminated dictionary")
            if char in (";", ","):
                self.index += 1
                continue
            key = self._parse_key()
            self._skip_trivia()
            self._expect("=")
            result[key] = self._parse_dict_value()
            self._skip_trivia()
            char = self._peek_raw()
            if char == ";":
                self.index += 1
            elif char != "}":
                self._fail("expected ';' or '}' after a dictionary value")

    def _parse_unbraced_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        while True:
            self._skip_trivia()
            if not self._peek_raw():
                return result
            if self._peek_raw() in (";", ","):
                self.index += 1
                continue
            key = self._parse_key()
            self._skip_trivia()
            self._expect("=")
            result[key] = self._parse_dict_value()
            self._skip_trivia()
            if self._peek_raw() == ";":
                self.index += 1
            elif self._peek_raw():
                self._fail("expected ';' after a dictionary value")

    def _parse_dict_value(self) -> Any:
        """Accept Glyphs' occasional unquoted comma-separated scalar lists."""
        values = [self._parse_value()]
        self._skip_trivia()
        while self._peek_raw() == ",":
            self.index += 1
            self._skip_trivia()
            if self._peek_raw() in ("", ";", "}"):
                break
            values.append(self._parse_value())
            self._skip_trivia()
        if len(values) == 1:
            return values[0]
        return values

    def _parse_array(self) -> List[Any]:
        self._expect("(")
        result: List[Any] = []
        while True:
            self._skip_trivia()
            char = self._peek_raw()
            if char == ")":
                self.index += 1
                return result
            if not char:
                self._fail("unterminated array")
            if char in (",", ";"):
                self.index += 1
                continue
            result.append(self._parse_value())
            self._skip_trivia()
            char = self._peek_raw()
            if char in (",", ";"):
                self.index += 1
            elif char != ")":
                # Whitespace-separated array items occur in some hand-edited
                # files, so the next loop iteration is allowed to parse one.
                continue

    def _parse_key(self) -> str:
        self._skip_trivia()
        if self._peek_raw() == '"':
            return self._parse_quoted()
        key = self._parse_bare()
        if not key:
            self._fail("expected a dictionary key")
        return key

    def _parse_bare(self) -> str:
        start = self.index
        while self.index < self.length:
            char = self.text[self.index]
            if char.isspace() or char in "{}()=;,":
                break
            if char == "/" and self.index + 1 < self.length:
                if self.text[self.index + 1] in ("/", "*"):
                    break
            self.index += 1
        if self.index == start:
            self._fail("expected an unquoted value")
        return self.text[start : self.index]

    def _parse_quoted(self) -> str:
        self._expect('"')
        chars: List[str] = []
        simple_escapes = {
            '"': '"',
            "\\": "\\",
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "b": "\b",
            "f": "\f",
        }
        while self.index < self.length:
            char = self.text[self.index]
            self.index += 1
            if char == '"':
                return "".join(chars)
            if char != "\\":
                chars.append(char)
                continue
            if self.index >= self.length:
                self._fail("unterminated escape sequence")
            escaped = self.text[self.index]
            self.index += 1
            if escaped in simple_escapes:
                chars.append(simple_escapes[escaped])
            elif escaped in ("u", "U"):
                chars.append(self._parse_hex_escape(4))
            elif escaped == "x":
                chars.append(self._parse_hex_escape(2))
            elif escaped in "01234567":
                digits = escaped
                while (
                    len(digits) < 3
                    and self.index < self.length
                    and self.text[self.index] in "01234567"
                ):
                    digits += self.text[self.index]
                    self.index += 1
                chars.append(chr(int(digits, 8)))
            elif escaped == "\n":
                continue
            elif escaped == "\r":
                if self._peek_raw() == "\n":
                    self.index += 1
            else:
                # OpenStep readers conventionally accept escaped punctuation.
                chars.append(escaped)
        self._fail("unterminated quoted string")
        return ""

    def _parse_hex_escape(self, length: int) -> str:
        end = self.index + length
        digits = self.text[self.index : end]
        if len(digits) != length or any(
            char not in "0123456789abcdefABCDEF" for char in digits
        ):
            self._fail("invalid hexadecimal escape")
        self.index = end
        return chr(int(digits, 16))

    def _skip_trivia(self) -> None:
        while True:
            while self.index < self.length and self.text[self.index].isspace():
                self.index += 1
            if self.text.startswith("//", self.index):
                newline = self.text.find("\n", self.index + 2)
                self.index = self.length if newline < 0 else newline + 1
                continue
            if self.text.startswith("/*", self.index):
                end = self.text.find("*/", self.index + 2)
                if end < 0:
                    self._fail("unterminated block comment")
                self.index = end + 2
                continue
            if self._peek_raw() == "#":
                newline = self.text.find("\n", self.index + 1)
                self.index = self.length if newline < 0 else newline + 1
                continue
            return

    def _expect(self, expected: str) -> None:
        self._skip_trivia()
        if self._peek_raw() != expected:
            self._fail("expected {!r}".format(expected))
        self.index += 1

    def _peek_raw(self) -> str:
        if self.index >= self.length:
            return ""
        return self.text[self.index]

    def _fail(self, message: str) -> None:
        line = self.text.count("\n", 0, self.index) + 1
        line_start = self.text.rfind("\n", 0, self.index) + 1
        column = self.index - line_start + 1
        raise PlistParseError("{} at line {}, column {}".format(message, line, column))


def loads(text: str) -> Dict[str, Any]:
    """Parse a Glyphs/OpenStep property list from text."""
    return _Parser(text).parse()


def load(source: Union[str, os.PathLike]) -> Dict[str, Any]:
    """Parse a property list path or an already-open text stream."""
    if hasattr(source, "read"):
        return loads(source.read())
    with open(source, "r", encoding="utf-8") as stream:
        return loads(stream.read())


parse = loads


__all__ = ["PlistParseError", "load", "loads", "parse"]
