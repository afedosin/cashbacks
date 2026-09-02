#!/usr/bin/env python3
"""Validate raw cashback source files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
import sys
import unicodedata
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRECTORY = "src"
RULE_FIELDS = {
    "category",
    "unified_category",
    "include_mcc",
    "exclude_mcc",
}
WINDOWS_UNSAFE_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_FILENAME_STEM = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z",
    re.IGNORECASE,
)
SELECTOR_FIELDS = ("include_mcc", "exclude_mcc")
BANK_DIRECTORY_NAME = re.compile(
    r"(?P<label>.+)-(?P<country>[a-z]{2})_(?P<bank_id>[1-9][0-9]*|)\Z"
)


class DataError(Exception):
    """An actionable source-data or repository-layout error."""


class DuplicateObjectKey(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class InvalidJsonConstant(ValueError):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.value = value


class InvalidJsonInteger(ValueError):
    """A syntactically valid JSON integer exceeds Python's digit limit."""


@dataclass(frozen=True)
class BankSource:
    bank_id: int
    path: Path
    entries: list[dict[str, Any]]


@dataclass(frozen=True)
class PendingBankSource:
    label: str
    path: Path
    entries: list[dict[str, Any]]


@dataclass(frozen=True)
class RepositorySources:
    banks: list[BankSource]
    pending_banks: list[PendingBankSource]


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _quoted(value: str) -> str:
    quoted = json.dumps(value, ensure_ascii=False)
    return quoted.encode("utf-8", errors="backslashreplace").decode("utf-8")


def normalize_category_title(title: str) -> str:
    title = unicodedata.normalize("NFC", title)
    title = re.sub(r"[^\w\s]", "", title, flags=re.UNICODE)
    title = re.sub(r"[_\s]+", " ", title)
    return title.strip().lower()


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateObjectKey(key)
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise InvalidJsonConstant(value)


def _parse_json_integer(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise InvalidJsonInteger from exc


def _parse_json(path: Path, root: Path) -> Any:
    display_path = _display_path(path, root)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataError(f"{display_path}: cannot read file: {reason}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataError(
            f"{display_path}: invalid UTF-8 at byte {exc.start + 1}"
        ) from exc

    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
            parse_int=_parse_json_integer,
        )
    except DuplicateObjectKey as exc:
        raise DataError(
            f"{display_path}: duplicate JSON object key {_quoted(exc.key)}"
        ) from exc
    except InvalidJsonConstant as exc:
        raise DataError(
            f"{display_path}: invalid JSON constant {exc.value}"
        ) from exc
    except InvalidJsonInteger as exc:
        raise DataError(
            f"{display_path}: JSON integer is too long to parse"
        ) from exc
    except RecursionError as exc:
        raise DataError(
            f"{display_path}: JSON structure is too deeply nested"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DataError(
            f"{display_path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc


def _validate_trimmed_string(value: Any, location: str) -> str:
    if type(value) is not str:
        raise DataError(f"{location} must be a string")
    if not value or value != value.strip():
        raise DataError(f"{location} must be non-empty and have no surrounding whitespace")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DataError(f"{location} contains an invalid Unicode surrogate") from exc
    return value


def _validate_nfc_string(value: Any, location: str) -> str:
    validated = _validate_trimmed_string(value, location)
    normalized = unicodedata.normalize("NFC", validated)
    if validated != normalized:
        raise DataError(
            f"{location} must be Unicode NFC-normalized; "
            f"expected {_quoted(normalized)}"
        )
    return validated


def _validate_mcc_list(value: Any, location: str) -> list[int]:
    if type(value) is not list or not value:
        raise DataError(f"{location} must be a non-empty array")

    validated: list[int] = []
    seen: set[int] = set()
    previous: int | None = None
    for index, mcc in enumerate(value):
        item_location = f"{location}[{index}]"
        if type(mcc) is not int:
            raise DataError(f"{item_location} must be a JSON integer")
        if not 1 <= mcc <= 9999:
            raise DataError(f"{item_location} must be between 1 and 9999")
        if mcc in seen:
            raise DataError(f"{item_location} duplicates MCC {mcc}")
        if previous is not None and mcc <= previous:
            raise DataError(
                f"{location} must be strictly ascending; {mcc} follows {previous}"
            )
        seen.add(mcc)
        validated.append(mcc)
        previous = mcc
    return validated


def _validate_category_aliases(
    value: Any, location: str
) -> tuple[Any, list[tuple[str, str, str]]]:
    if type(value) is str:
        alias = _validate_nfc_string(value, location)
        normalized = normalize_category_title(alias)
        if not normalized:
            raise DataError(
                f"{location}: category alias {_quoted(alias)} has an empty normalized key"
            )
        return alias, [(alias, normalized, location)]
    if type(value) is not list or not value:
        raise DataError(f"{location} must be a string or a non-empty array of strings")

    aliases: list[str] = []
    normalized_aliases: list[tuple[str, str, str]] = []
    indexes: dict[str, tuple[str, int]] = {}
    for index, raw_alias in enumerate(value):
        item_location = f"{location}[{index}]"
        alias = _validate_nfc_string(raw_alias, item_location)
        normalized = normalize_category_title(alias)
        if not normalized:
            raise DataError(
                f"{item_location}: category alias {_quoted(alias)} has an empty "
                "normalized key"
            )
        first = indexes.get(normalized)
        if first is not None:
            first_alias, first_index = first
            if alias == first_alias:
                raise DataError(
                    f"{item_location} duplicates category alias {_quoted(alias)} "
                    f"from index {first_index}"
                )
            raise DataError(
                f"{item_location}: category alias {_quoted(alias)} normalizes to "
                f"{_quoted(normalized)} and conflicts with {_quoted(first_alias)} at "
                f"{location}[{first_index}]"
            )
        indexes[normalized] = (alias, index)
        aliases.append(alias)
        normalized_aliases.append((alias, normalized, item_location))
    return aliases, normalized_aliases


def _validate_category_basename(name: str, location: str) -> str:
    if type(name) is not str:
        raise DataError(f"{location}: category filename must be a string")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise DataError(f"{location}: category filename must be one portable basename")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DataError(f"{location}: category filename contains an invalid Unicode surrogate") from exc
    if name != unicodedata.normalize("NFC", name):
        raise DataError(f"{location}: category filename must be Unicode NFC-normalized")
    if not name.endswith(".json"):
        raise DataError(f"{location}: category filename must end in .json")

    stem = name[: -len(".json")]
    if not stem:
        raise DataError(f"{location}: category filename must have a non-empty stem")
    if stem[-1] in {" ", "."}:
        raise DataError(
            f"{location}: category filename stem must not end with a space or period"
        )
    if any(
        character in WINDOWS_UNSAFE_FILENAME_CHARACTERS
        or unicodedata.category(character) == "Cc"
        for character in name
    ):
        raise DataError(f"{location}: category filename contains a reserved character")
    if WINDOWS_RESERVED_FILENAME_STEM.fullmatch(stem.partition(".")[0]):
        raise DataError(f"{location}: category filename uses a reserved Windows stem")
    return name

def _canonical_category_filename(alias: str) -> str:
    stem_characters: list[str] = []
    replacing_run = False
    for character in alias:
        replace = (
            character.isspace()
            or unicodedata.category(character) == "Cc"
            or character in WINDOWS_UNSAFE_FILENAME_CHARACTERS
        )
        if replace:
            if not replacing_run:
                stem_characters.append(" ")
        else:
            stem_characters.append(character)
        replacing_run = replace

    stem = "".join(stem_characters).strip(" .")
    first_component, separator, remainder = stem.partition(".")
    if WINDOWS_RESERVED_FILENAME_STEM.fullmatch(first_component):
        stem = f"{first_component}_{separator}{remainder}"
    return f"{stem}.json"


def _validate_category_rule(
    value: Any, path: Path, root: Path
) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
    display_path = _display_path(path, root)
    if type(value) is not dict:
        raise DataError(f"{display_path}: root value must be an object")

    unknown_fields = sorted(set(value) - RULE_FIELDS)
    if unknown_fields:
        fields = ", ".join(_quoted(field) for field in unknown_fields)
        raise DataError(f"{display_path}: unknown field(s): {fields}")
    if "category" not in value:
        raise DataError(f'{display_path}: missing required field "category"')
    if next(iter(value)) != "category":
        raise DataError(f'{display_path}: "category" must be the first field')

    category, aliases = _validate_category_aliases(
        value["category"], f"{display_path}.category"
    )
    selectors = [field for field in SELECTOR_FIELDS if field in value]
    if len(selectors) != 1:
        state = "both are present" if len(selectors) == 2 else "neither is present"
        raise DataError(
            f'{display_path}: must contain exactly one of "include_mcc" and '
            f'"exclude_mcc"; {state}'
        )
    selector = selectors[0]
    expected_fields = (
        ("category", "unified_category", selector)
        if "unified_category" in value
        else ("category", selector)
    )
    if tuple(value) != expected_fields:
        fields = ", ".join(_quoted(field) for field in expected_fields)
        raise DataError(f"{display_path}: fields must be ordered as {fields}")

    entry: dict[str, Any] = {"category": category}
    if "unified_category" in value:
        entry["unified_category"] = _validate_nfc_string(
            value["unified_category"], f"{display_path}.unified_category"
        )
    entry[selector] = _validate_mcc_list(value[selector], f"{display_path}.{selector}")
    return entry, aliases


def _parse_bank_directory(path: Path, root: Path) -> tuple[str, str, int | None]:
    display_path = _display_path(path, root)
    match = BANK_DIRECTORY_NAME.fullmatch(path.name)
    if match is None or match["label"] != match["label"].strip():
        raise DataError(
            f"{display_path}: malformed bank directory name; expected "
            "<trimmed-label>-<two-lowercase-ascii-cc>_"
            "<positive-decimal-bank-id-or-empty>"
        )
    bank_id_text = match["bank_id"]
    if not bank_id_text:
        return match["label"], match["country"], None
    try:
        return match["label"], match["country"], int(bank_id_text)
    except ValueError as exc:
        raise DataError(
            f"{display_path}: bank ID is too long to parse as a decimal integer"
        ) from exc


def _categories_directory(bank_directory: Path, root: Path) -> Path:
    display_directory = _display_path(bank_directory, root)
    categories_directory = bank_directory / "categories"
    display_categories = _display_path(categories_directory, root)
    try:
        children = sorted(bank_directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataError(
            f"{display_directory}: cannot list bank directory: {reason}"
        ) from exc

    if not children:
        raise DataError(f"{display_categories}: required categories directory is missing")
    if len(children) != 1 or children[0].name != "categories":
        unexpected = next(
            (child for child in children if child.name != "categories"), children[0]
        )
        raise DataError(
            f"{_display_path(unexpected, root)}: unexpected bank directory entry; "
            "expected categories as the sole child"
        )

    try:
        categories_status = categories_directory.lstat()
    except FileNotFoundError as exc:
        raise DataError(
            f"{display_categories}: required categories directory is missing"
        ) from exc
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataError(
            f"{display_categories}: cannot inspect categories directory: {reason}"
        ) from exc
    if stat.S_ISLNK(categories_status.st_mode):
        raise DataError(f"{display_categories}: categories directory must not be a symlink")
    if not stat.S_ISDIR(categories_status.st_mode):
        raise DataError(f"{display_categories}: categories path must be a directory")
    return categories_directory


def _load_category_files(
    categories_directory: Path, root: Path
) -> list[dict[str, Any]]:
    display_directory = _display_path(categories_directory, root)
    try:
        paths = sorted(categories_directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataError(
            f"{display_directory}: cannot list category files: {reason}"
        ) from exc
    if not paths:
        raise DataError(f"{display_directory}: no category files found")

    source_paths: list[Path] = []
    for path in paths:
        display_path = _display_path(path, root)
        try:
            path_status = path.lstat()
        except OSError as exc:
            reason = exc.strerror or str(exc)
            raise DataError(
                f"{display_path}: cannot inspect category file: {reason}"
            ) from exc
        if stat.S_ISLNK(path_status.st_mode):
            raise DataError(f"{display_path}: category file must not be a symlink")
        if not stat.S_ISREG(path_status.st_mode):
            raise DataError(f"{display_path}: category entry must be a regular file")
        _validate_category_basename(path.name, display_path)
        source_paths.append(path)

    aliases: dict[str, tuple[str, Path, str]] = {}
    entries: list[dict[str, Any]] = []
    for path in source_paths:
        display_path = _display_path(path, root)
        entry, rule_aliases = _validate_category_rule(_parse_json(path, root), path, root)
        first_alias = rule_aliases[0][0]
        expected_filename = _canonical_category_filename(first_alias)
        if path.name != expected_filename:
            raise DataError(
                f"{display_path}: category filename must be "
                f"{_quoted(expected_filename)} for first category alias "
                f"{_quoted(first_alias)}"
            )
        for alias, normalized, alias_location in rule_aliases:
            first = aliases.get(normalized)
            if first is not None:
                first_alias, first_path, first_location = first
                if alias == first_alias:
                    raise DataError(
                        f"{display_path}: duplicate category alias {_quoted(alias)}; "
                        f"already defined by {_display_path(first_path, root)}"
                    )
                raise DataError(
                    f"{alias_location}: category alias {_quoted(alias)} normalizes to "
                    f"{_quoted(normalized)} and conflicts with {_quoted(first_alias)} "
                    f"at {first_location}"
                )
            aliases[normalized] = (alias, path, alias_location)
        entries.append(entry)
    return entries


def load_repository(root: Path = ROOT) -> RepositorySources:
    root = Path(root)
    source_directory = root / SOURCE_DIRECTORY
    try:
        source_status = source_directory.lstat()
    except FileNotFoundError as exc:
        raise DataError("src: source directory does not exist") from exc
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataError(f"src: cannot inspect source directory: {reason}") from exc
    if stat.S_ISLNK(source_status.st_mode):
        raise DataError("src: source directory must not be a symlink")
    if not stat.S_ISDIR(source_status.st_mode):
        raise DataError("src: source path is not a directory")

    try:
        bank_directories = sorted(source_directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataError(f"src: cannot list bank directories: {reason}") from exc
    if not bank_directories:
        raise DataError("src: no bank source directories found")

    identified: list[BankSource] = []
    pending: list[PendingBankSource] = []
    by_bank_id: dict[int, Path] = {}
    for bank_directory in bank_directories:
        display_path = _display_path(bank_directory, root)
        try:
            child_status = bank_directory.lstat()
        except OSError as exc:
            reason = exc.strerror or str(exc)
            raise DataError(
                f"{display_path}: cannot inspect bank directory: {reason}"
            ) from exc
        if stat.S_ISLNK(child_status.st_mode):
            raise DataError(f"{display_path}: bank directory must not be a symlink")
        if not stat.S_ISDIR(child_status.st_mode):
            raise DataError(f"{display_path}: bank source entry must be a directory")

        label, country, bank_id = _parse_bank_directory(bank_directory, root)
        categories_directory = _categories_directory(bank_directory, root)
        if bank_id is not None and bank_id in by_bank_id:
            first_path = _display_path(by_bank_id[bank_id], root)
            raise DataError(
                f"{display_path}: duplicate bank ID {bank_id}; already used by {first_path}"
            )
        entries = _load_category_files(categories_directory, root)
        if bank_id is None:
            pending.append(
                PendingBankSource(
                    label=f"{label}-{country}",
                    path=categories_directory,
                    entries=entries,
                )
            )
            continue
        by_bank_id[bank_id] = bank_directory
        identified.append(
            BankSource(bank_id=bank_id, path=categories_directory, entries=entries)
        )

    sources = sorted(identified, key=lambda source: source.bank_id)
    pending_sources = sorted(pending, key=lambda source: source.label)
    return RepositorySources(banks=sources, pending_banks=pending_sources)


def load_sources(root: Path = ROOT) -> list[BankSource]:
    """Load only active source banks; pending banks remain inactive."""
    return load_repository(root).banks


def _entry_count(sources: Sequence[BankSource]) -> int:
    return sum(len(source.entries) for source in sources)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate cashback source files.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate every source file")
    pending_parser = subparsers.add_parser(
        "pending", help="list validated pending bank paths"
    )
    pending_parser.add_argument(
        "--json", action="store_true", required=True, help="emit a JSON array"
    )
    return parser


def _os_error_message(exc: OSError, root: Path) -> str:
    reason = exc.strerror or str(exc)
    if exc.filename is None:
        return reason
    return f"{_display_path(Path(exc.filename), root)}: {reason}"


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    repository_root = ROOT if root is None else Path(root)

    try:
        repository = load_repository(repository_root)
        if args.command == "pending":
            pending_paths = sorted(
                _display_path(source.path, repository_root)
                for source in repository.pending_banks
            )
            print(json.dumps(pending_paths, ensure_ascii=False))
            return 0
        bank_count = len(repository.banks)
        entry_count = _entry_count(repository.banks)
        pending_count = len(repository.pending_banks)
        if args.command == "validate":
            print(
                f"Validated {bank_count} banks and {entry_count} entries; "
                f"{pending_count} pending banks."
            )
            return 0
        raise AssertionError(f"unexpected command {args.command!r}")
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {_os_error_message(exc, repository_root)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
