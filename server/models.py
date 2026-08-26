"""Shared immutable state and response encodings for the cashback service."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from scripts import cashbacks


@dataclass(frozen=True, slots=True)
class Config:
    checkout: Path
    repository_url: str
    github_token: str | None = field(default=None, repr=False)
    host: str = "0.0.0.0"
    port: int = 8080
    remote: str = "origin"
    ref: str = "main"
    git_timeout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class BankSnapshot:
    categories_body: bytes
    aliases: Mapping[str, bytes]

    def match_body(self, categories: Sequence[str]) -> bytes:
        return b"[" + b",".join(
            self.aliases.get(cashbacks.normalize_category_title(value), b"null")
            for value in categories
        ) + b"]"


@dataclass(frozen=True, slots=True)
class Snapshot:
    revision: str
    banks: Mapping[int, BankSnapshot]
    banks_body: bytes



@dataclass(frozen=True, slots=True)
class RefSelection:
    kind: str
    full_name: str


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def build_snapshot(sources: cashbacks.RepositorySources, revision: str) -> Snapshot:
    banks: dict[int, BankSnapshot] = {}
    for source in sources.banks:
        encoded_rules: list[bytes] = []
        aliases: dict[str, bytes] = {}
        for entry in source.entries:
            encoded = _json_bytes(entry)
            encoded_rules.append(encoded)
            category = entry["category"]
            values = (category,) if type(category) is str else tuple(category)
            for alias in values:
                aliases[cashbacks.normalize_category_title(alias)] = encoded
        categories_body = (
            b'{"company_id":'
            + str(source.bank_id).encode("ascii")
            + b',"categories":['
            + b",".join(encoded_rules)
            + b"]}"
        )
        banks[source.bank_id] = BankSnapshot(
            categories_body=categories_body,
            aliases=MappingProxyType(aliases),
        )
    return Snapshot(
        revision=revision,
        banks=MappingProxyType(banks),
        banks_body=_json_bytes(list(banks)),
    )
