import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unicodedata
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cashbacks.py"
SPEC = importlib.util.spec_from_file_location("cashbacks_cli", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
cashbacks = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cashbacks
SPEC.loader.exec_module(cashbacks)

REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


class CashbacksCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source_directory = self.root / "src"
        self.source_directory.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_entry(
        self,
        directory_name,
        filename,
        entry,
        *,
        source_directory=None,
    ):
        source_directory = source_directory or self.source_directory
        categories_directory = source_directory / directory_name / "categories"
        categories_directory.mkdir(parents=True, exist_ok=True)
        path = categories_directory / filename
        path.write_text(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return path

    def write_raw_entry(
        self,
        directory_name,
        filename,
        text,
        *,
        source_directory=None,
    ):
        source_directory = source_directory or self.source_directory
        categories_directory = source_directory / directory_name / "categories"
        categories_directory.mkdir(parents=True, exist_ok=True)
        path = categories_directory / filename
        path.write_text(text, encoding="utf-8")
        return path

    def run_cli(self, *arguments, root=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = cashbacks.main(arguments, root=self.root if root is None else root)
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_validation_error(self, expected_message, *, root=None):
        status, stdout, stderr = self.run_cli("validate", root=root)
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn(expected_message, stderr)
        self.assertNotIn("Traceback", stderr)
        stderr.encode("utf-8")
        return stderr

    def temporary_source_root(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        source_directory = root / "src"
        source_directory.mkdir()
        return root, source_directory

    def test_validate_accepts_canonical_scalar_filename_and_preserves_rule(self):
        self.write_entry(
            "Example Bank-ru_5044",
            "Groceries.json",
            {
                "category": "Groceries",
                "unified_category": "Food",
                "include_mcc": [5411],
            },
        )

        status, stdout, stderr = self.run_cli("validate")

        self.assertEqual(0, status)
        self.assertEqual("Validated 1 banks and 1 entries; 0 pending banks.\n", stdout)
        self.assertEqual("", stderr)
        entry = cashbacks.load_sources(self.root)[0].entries[0]
        self.assertEqual("Groceries", entry["category"])
        self.assertEqual("Food", entry["unified_category"])

    def test_array_filename_uses_only_first_alias_and_preserves_rule(self):
        aliases = ["Primary Category", "Later/Alias"]
        self.write_entry(
            "Bank-ru_3",
            "Primary Category.json",
            {
                "category": aliases,
                "unified_category": "Unified/Category",
                "include_mcc": [1],
            },
        )

        entry = cashbacks.load_sources(self.root)[0].entries[0]

        self.assertEqual(aliases, entry["category"])
        self.assertEqual("Unified/Category", entry["unified_category"])

    def test_canonical_filename_projection_through_loader(self):
        cases = (
            (
                "unsafe control and whitespace run",
                "Fuel<>:\x07\u2003\tCard",
                "Fuel Card.json",
            ),
            ("edge dots and replacement spaces", "..<Premium>..", "Premium.json"),
            ("reserved stem", "CON", "CON_.json"),
            ("reserved first component", "con.rules", "con_.rules.json"),
            (
                "preserved case and safe punctuation",
                "Travel-Rewards_2026!",
                "Travel-Rewards_2026!.json",
            ),
        )
        for kind, category, filename in cases:
            with self.subTest(kind=kind):
                root, source_directory = self.temporary_source_root()
                self.write_entry(
                    "Bank-ru_3",
                    filename,
                    {"category": category, "include_mcc": [1]},
                    source_directory=source_directory,
                )

                entry = cashbacks.load_sources(root)[0].entries[0]

                self.assertEqual(category, entry["category"])

    def test_rejects_noncanonical_filename_with_actionable_diagnostic(self):
        category = "Offers\x07Now"
        path = self.write_entry(
            "Bank-ru_3",
            "offers now.json",
            {"category": category, "include_mcc": [1]},
        )

        error = self.assert_validation_error("category filename must be")

        self.assertIn(path.relative_to(self.root).as_posix(), error)
        self.assertIn('"Offers Now.json"', error)
        self.assertIn('"Offers\\u0007Now"', error)

    def test_load_repository_orders_active_and_pending_and_load_sources_excludes_pending(self):
        self.write_entry(
            "Zulu-ru_12", "Zulu.json", {"category": "Zulu", "include_mcc": [12]}
        )
        self.write_entry(
            "Alpha-kz_3", "Alpha.json", {"category": "Alpha", "include_mcc": [3]}
        )
        self.write_entry(
            "Zulu-ru_", "Pending Z.json", {"category": "Pending Z", "include_mcc": [2]}
        )
        self.write_entry(
            "Alpha-by_", "Pending A.json", {"category": "Pending A", "exclude_mcc": [1]}
        )

        repository = cashbacks.load_repository(self.root)

        self.assertEqual([3, 12], [source.bank_id for source in repository.banks])
        self.assertEqual(["Alpha-by", "Zulu-ru"], [source.label for source in repository.pending_banks])
        self.assertEqual(repository.banks, cashbacks.load_sources(self.root))
        self.assertEqual(["Pending A"], [entry["category"] for entry in repository.pending_banks[0].entries])

    def test_pending_json_emits_only_sorted_repository_relative_paths(self):
        self.write_entry(
            "Active-ru_7", "Active.json", {"category": "Active", "include_mcc": [7]}
        )
        self.write_entry(
            "Zulu-ru_", "Zulu.json", {"category": "Zulu", "include_mcc": [2]}
        )
        self.write_entry(
            "Alpha-by_", "Alpha.json", {"category": "Alpha", "exclude_mcc": [1]}
        )

        status, stdout, stderr = self.run_cli("pending", "--json")

        self.assertEqual(0, status)
        self.assertEqual(
            [
                "src/Alpha-by_/categories",
                "src/Zulu-ru_/categories",
            ],
            json.loads(stdout),
        )
        self.assertEqual("", stderr)

    def test_pending_json_emits_empty_array_for_active_only_repository(self):
        self.write_entry(
            "Active-ru_7", "Active.json", {"category": "Active", "include_mcc": [7]}
        )

        status, stdout, stderr = self.run_cli("pending", "--json")

        self.assertEqual(0, status)
        self.assertEqual([], json.loads(stdout))
        self.assertEqual("", stderr)

    def test_pending_json_preserves_unicode_and_delimiter_like_path_text(self):
        directory_name = "Банк-->%`-ru_"
        self.write_entry(
            directory_name,
            "Offer.json",
            {"category": "Offer", "include_mcc": [1]},
        )

        status, stdout, stderr = self.run_cli("pending", "--json")

        self.assertEqual(0, status)
        self.assertEqual([f"src/{directory_name}/categories"], json.loads(stdout))
        self.assertEqual("", stderr)

    def test_pending_json_emits_nothing_when_active_or_pending_data_is_invalid(self):
        for kind, directory_name in (
            ("active", "Broken-ru_7"),
            ("pending", "Broken-ru_"),
        ):
            with self.subTest(kind=kind):
                root, source_directory = self.temporary_source_root()
                self.write_raw_entry(
                    directory_name,
                    "offer.json",
                    "[]\n",
                    source_directory=source_directory,
                )
                if kind == "active":
                    self.write_entry(
                        "Pending-ru_",
                        "Pending.json",
                        {"category": "Pending", "include_mcc": [1]},
                        source_directory=source_directory,
                    )

                status, stdout, stderr = self.run_cli(
                    "pending", "--json", root=root
                )

                self.assertEqual(1, status)
                self.assertEqual("", stdout)
                self.assertIn("root value must be an object", stderr)
                self.assertNotIn("Traceback", stderr)


    def test_rejects_every_invalid_bank_directory_grammar(self):
        invalid_names = (
            "Bank_3",
            "Bank-RU_3",
            "Bank-rу_3",
            "Bank-rus_3",
            "Bank-ru_0",
            "Bank-ru_03",
            "Bank-ru_-3",
            "Bank-ru",
            " Bank-ru_3",
        )
        for name in invalid_names:
            with self.subTest(name=name):
                root, source_directory = self.temporary_source_root()
                self.write_entry(
                    name,
                    "offer.json",
                    {"category": "Offer", "include_mcc": [1]},
                    source_directory=source_directory,
                )
                error = self.assert_validation_error(
                    "malformed bank directory name", root=root
                )
                self.assertIn(f"src/{name}", error)

    def test_rejects_legacy_source_child_without_fallback(self):
        bank_directory = self.source_directory / "Bank-ru_3"
        (bank_directory / "cashbacks").mkdir(parents=True)
        (bank_directory / "cashbacks" / "old.json").write_text(
            '{"category":"Old","include_mcc":[1]}\n', encoding="utf-8"
        )

        self.assert_validation_error("expected categories as the sole child")

    def test_rejects_duplicate_active_bank_id(self):
        entry = {"category": "Offer", "include_mcc": [1]}
        self.write_entry("First-ru_3", "Offer.json", entry)
        self.write_entry("Second-kz_3", "Offer.json", entry)

        self.assert_validation_error("duplicate bank ID 3; already used by src/First-ru_3")

    def test_array_aliases_preserve_input_order(self):
        aliases = ["Groceries", "Food"]
        self.write_entry(
            "Bank-ru_3",
            "Groceries.json",
            {"category": aliases, "include_mcc": [5411]},
        )

        entry = cashbacks.load_sources(self.root)[0].entries[0]

        self.assertEqual(aliases, entry["category"])

    def test_rejects_aliases_sharing_a_normalized_key(self):
        first = self.write_entry(
            "Bank-ru_3", "Fuel.json", {"category": "Fuel", "include_mcc": [1]}
        )
        second = self.write_entry(
            "Bank-ru_3",
            "Something else.json",
            {"category": ["Something else", "fuel"], "include_mcc": [2]},
        )

        error = self.assert_validation_error(
            'category alias "fuel" normalizes to "fuel" and conflicts with "Fuel" at'
        )
        self.assertIn(second.relative_to(self.root).as_posix() + ".category[1]", error)
        self.assertIn(first.relative_to(self.root).as_posix() + ".category", error)

    def test_rejects_invalid_alias_values(self):
        cases = (
            ([], "non-empty array"),
            ([""], "must be non-empty and have no surrounding whitespace"),
            ([" Food"], "must be non-empty and have no surrounding whitespace"),
            ([1], "must be a string"),
            ("Cafe\u0301", "Unicode NFC-normalized"),
        )
        for category, message in cases:
            with self.subTest(category=category):
                root, source_directory = self.temporary_source_root()
                self.write_entry(
                    "Bank-ru_3",
                    "offer.json",
                    {"category": category, "include_mcc": [1]},
                    source_directory=source_directory,
                )
                self.assert_validation_error(message, root=root)

    def test_rejects_aliases_with_empty_normalized_key(self):
        cases = (
            ("scalar", "!!!", ".category"),
            ("array element", ["Food", "!!!"], ".category[1]"),
        )
        for kind, category, location_suffix in cases:
            with self.subTest(kind=kind):
                root, source_directory = self.temporary_source_root()
                path = self.write_entry(
                    "Bank-ru_3",
                    "offer.json",
                    {"category": category, "include_mcc": [1]},
                    source_directory=source_directory,
                )

                error = self.assert_validation_error("empty normalized key", root=root)

                self.assertIn(
                    path.relative_to(root).as_posix() + location_suffix, error
                )

    def test_rejects_duplicate_alias_in_one_array(self):
        self.write_entry(
            "Bank-ru_3",
            "offer.json",
            {"category": ["Food", "Food"], "include_mcc": [1]},
        )

        error = self.assert_validation_error('duplicates category alias "Food" from index 0')

        self.assertIn("src/Bank-ru_3/categories/offer.json.category[1]", error)

    def test_rejects_distinct_aliases_with_same_normalized_key_in_one_array(self):
        path = self.write_entry(
            "Bank-ru_3",
            "offer.json",
            {"category": ["Food!", "food"], "include_mcc": [1]},
        )

        error = self.assert_validation_error(
            'category alias "food" normalizes to "food" and conflicts with "Food!" at'
        )
        self.assertIn(path.relative_to(self.root).as_posix() + ".category[1]", error)
        self.assertIn(path.relative_to(self.root).as_posix() + ".category[0]", error)

    def test_rejects_cross_rule_alias_collision_in_lexical_filename_order(self):
        first = self.write_entry(
            "Bank-ru_3",
            "A first.json",
            {"category": ["A first", "Food"], "include_mcc": [1]},
        )
        second = self.write_entry(
            "Bank-ru_3",
            "Z second.json",
            {"category": ["Z second", "Food"], "include_mcc": [2]},
        )

        error = self.assert_validation_error('duplicate category alias "Food"')

        self.assertIn(second.relative_to(self.root).as_posix(), error)
        self.assertIn(first.relative_to(self.root).as_posix(), error)

    def test_rejects_normalized_cross_rule_alias_collision_in_lexical_filename_order(self):
        first = self.write_entry(
            "Bank-ru_3",
            "A first.json",
            {"category": ["A first", "Fuel!"], "include_mcc": [1]},
        )
        second = self.write_entry(
            "Bank-ru_3",
            "Z second.json",
            {"category": ["Z second", "FUEL"], "include_mcc": [2]},
        )

        error = self.assert_validation_error(
            'category alias "FUEL" normalizes to "fuel" and conflicts with "Fuel!" at'
        )
        self.assertIn(second.relative_to(self.root).as_posix() + ".category[1]", error)
        self.assertIn(first.relative_to(self.root).as_posix() + ".category[1]", error)

    def test_allows_normalized_alias_reuse_across_active_and_pending_banks(self):
        self.write_entry(
            "Bank-ru_3", "Fuel.json", {"category": "Fuel", "include_mcc": [1]}
        )
        self.write_entry(
            "Bank-ru_", "fuel.json", {"category": "fuel", "include_mcc": [2]}
        )

        repository = cashbacks.load_repository(self.root)

        self.assertEqual(
            ["Fuel"], [entry["category"] for entry in repository.banks[0].entries]
        )
        self.assertEqual(
            ["fuel"], [entry["category"] for entry in repository.pending_banks[0].entries]
        )

    def test_category_basename_validator_rejects_every_unsafe_form(self):
        cases = (
            ("offer.txt", "must end in .json"),
            (".json", "non-empty stem"),
            (".", "portable basename"),
            ("..", "portable basename"),
            ("nested/offer.json", "portable basename"),
            ("nested\\offer.json", "portable basename"),
            ("offer\x1f.json", "reserved character"),
            ("offer\u0085.json", "reserved character"),
            ("offer?.json", "reserved character"),
            ("CON.json", "reserved Windows stem"),
            ("LPT1.extra.json", "reserved Windows stem"),
            ("offer .json", "must not end with a space or period"),
            ("Cafe\u0301.json", "Unicode NFC-normalized"),
        )
        for name, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(cashbacks.DataError, message):
                    cashbacks._validate_category_basename(name, "category file")

    def test_rejects_unsafe_category_filename_before_parsing(self):
        path = self.write_raw_entry(
            "Bank-ru_3", "CON.json", "this is not JSON\n"
        )

        error = self.assert_validation_error("reserved Windows stem")

        self.assertIn(path.relative_to(self.root).as_posix(), error)

    def test_rejects_both_and_neither_selector(self):
        cases = (
            (
                {"category": "Both", "include_mcc": [1], "exclude_mcc": [2]},
                "both are present",
            ),
            ({"category": "Neither"}, "neither is present"),
        )
        for entry, message in cases:
            with self.subTest(entry=entry):
                root, source_directory = self.temporary_source_root()
                self.write_entry(
                    "Bank-ru_3", "offer.json", entry, source_directory=source_directory
                )
                self.assert_validation_error(message, root=root)

    def test_rejects_invalid_mcc_values(self):
        cases = (
            ([10, 10], "duplicates MCC 10"),
            ([20, 10], "must be strictly ascending"),
            ([10000], "must be between 1 and 9999"),
            ([True], "must be a JSON integer"),
            ([1.5], "must be a JSON integer"),
        )
        for values, message in cases:
            with self.subTest(values=values):
                root, source_directory = self.temporary_source_root()
                self.write_entry(
                    "Bank-ru_3",
                    "offer.json",
                    {"category": "Offer", "include_mcc": values},
                    source_directory=source_directory,
                )
                self.assert_validation_error(message, root=root)

    def test_rejects_unknown_fields_and_invalid_field_order(self):
        cases = (
            (
                '{"category":"Offer","include_mcc":[1],"unexpected":true}\n',
                "unknown field(s): \"unexpected\"",
            ),
            (
                '{"include_mcc":[1],"category":"Offer"}\n',
                '"category" must be the first field',
            ),
            (
                '{"category":"Offer","include_mcc":[1],"unified_category":"Food"}\n',
                "fields must be ordered as",
            ),
        )
        for text, message in cases:
            with self.subTest(text=text):
                root, source_directory = self.temporary_source_root()
                self.write_raw_entry(
                    "Bank-ru_3", "offer.json", text, source_directory=source_directory
                )
                self.assert_validation_error(message, root=root)

    def test_rejects_duplicate_json_object_keys(self):
        self.write_raw_entry(
            "Bank-ru_3",
            "offer.json",
            '{"category":"Offer","include_mcc":[1],"include_mcc":[2]}\n',
        )

        self.assert_validation_error('duplicate JSON object key "include_mcc"')

    def test_rejects_empty_categories_directory_and_nonregular_entry(self):
        categories_directory = self.source_directory / "Bank-ru_3" / "categories"
        categories_directory.mkdir(parents=True)
        self.assert_validation_error("no category files found")

        categories_directory.mkdir(exist_ok=True)
        (categories_directory / "nested.json").mkdir()
        self.assert_validation_error("category entry must be a regular file")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are not supported")
    def test_rejects_symlinked_categories_directory(self):
        bank_directory = self.source_directory / "Bank-ru_3"
        bank_directory.mkdir()
        target = self.root / "real-categories"
        target.mkdir()
        (bank_directory / "categories").symlink_to(target, target_is_directory=True)

        self.assert_validation_error("categories directory must not be a symlink")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are not supported")
    def test_rejects_symlinked_category_file(self):
        categories_directory = self.source_directory / "Bank-ru_3" / "categories"
        categories_directory.mkdir(parents=True)
        target = self.root / "rule.json"
        target.write_text('{"category":"Offer","include_mcc":[1]}\n', encoding="utf-8")
        (categories_directory / "offer.json").symlink_to(target)

        self.assert_validation_error("category file must not be a symlink")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are not supported")
    def test_rejects_symlinked_bank_and_source_directories(self):
        target_bank = self.root / "real-bank"
        target_bank.mkdir()
        (self.source_directory / "Bank-ru_3").symlink_to(
            target_bank, target_is_directory=True
        )
        self.assert_validation_error("bank directory must not be a symlink")

        (self.source_directory / "Bank-ru_3").unlink()
        self.source_directory.rmdir()
        real_source_directory = self.root / "real-src"
        real_source_directory.mkdir()
        self.source_directory.symlink_to(real_source_directory, target_is_directory=True)
        self.assert_validation_error("source directory must not be a symlink")

    def test_rejects_malformed_json_and_nonobject_roots(self):
        cases = (
            ("NaN\n", "invalid JSON constant NaN"),
            ("[]\n", "root value must be an object"),
        )
        for text, message in cases:
            with self.subTest(text=text):
                root, source_directory = self.temporary_source_root()
                self.write_raw_entry(
                    "Bank-ru_3", "offer.json", text, source_directory=source_directory
                )
                self.assert_validation_error(message, root=root)

    def test_invalid_diagnostics_are_stable_and_repository_relative(self):
        first = self.write_raw_entry(
            "Bank-ru_3", "a-first.json", '{"category":"First"}\n'
        )
        self.write_raw_entry("Bank-ru_3", "z-second.json", "[]\n")

        first_result = self.run_cli("validate")
        second_result = self.run_cli("validate")

        self.assertEqual(first_result, second_result)
        self.assertEqual(1, first_result[0])
        self.assertIn(first.relative_to(self.root).as_posix(), first_result[2])
        self.assertNotIn(str(self.root), first_result[2])

    def test_bank_diagnostics_follow_lexical_directory_order(self):
        first = self.write_raw_entry("Alpha-ru_12", "rule.json", "[]\n")
        self.write_raw_entry(
            "Zulu-ru_3", "rule.json", '{"category":"Missing selector"}\n'
        )

        error = self.assert_validation_error("root value must be an object")

        self.assertIn(first.relative_to(self.root).as_posix(), error)

    def test_unversioned_source_schema_directly_declares_scalar_and_array_aliases(self):
        schema_directory = REPOSITORY_ROOT / "schema"
        source_schema = json.loads(
            (schema_directory / "cashback-category-source.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertNotIn("$ref", source_schema)
        self.assertEqual("object", source_schema["type"])
        self.assertFalse((schema_directory / "cashback-category-rule.v1.schema.json").exists())

        category_options = source_schema["properties"]["category"]["oneOf"]
        self.assertEqual("#/$defs/alias", category_options[0]["$ref"])
        self.assertEqual("array", category_options[1]["type"])
        self.assertEqual(1, category_options[1]["minItems"])
        self.assertTrue(category_options[1]["uniqueItems"])
        self.assertEqual("#/$defs/alias", category_options[1]["items"]["$ref"])

    def test_cli_help_retains_validate_command(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                cashbacks.main(("--help",), root=self.root)

        self.assertEqual(0, raised.exception.code)
        self.assertIn("validate", stdout.getvalue())

    def test_rejects_non_nfc_unified_category(self):
        decomposed = unicodedata.normalize("NFD", "Café")
        self.write_entry(
            "Bank-ru_3",
            "offer.json",
            {
                "category": "Offer",
                "unified_category": decomposed,
                "include_mcc": [1],
            },
        )

        self.assert_validation_error("unified_category must be Unicode NFC-normalized")


if __name__ == "__main__":
    unittest.main()
