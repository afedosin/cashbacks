# Cashback mappings

Репозиторий содержит поддерживаемые сообществом правила сопоставления банковских категорий кешбэка с MCC-кодами. Единственный канонический артефакт данных этого репозитория — проверенные исходные файлы в checkout конкретной ревизии; другой артефакт данных не публикуется.

[Русский](#russian) | [English](#english)

<a id="russian"></a>
## Русский

### Структура данных

Каждый банк хранится только в одном каталоге `categories/`:

```text
src/<человекочитаемая метка>-<cc>_<bank-id>/categories/<имя-по-первой-категории>.json
src/<человекочитаемая метка>-<cc>_/categories/<имя-по-первой-категории>.json
```

`<человекочитаемая метка>` непуста и без пробелов по краям; `<cc>` — ровно две строчные ASCII-буквы. Последний `_` отделяет идентификатор: положительный десятичный `bank-id` не имеет ведущих нулей и делает банк активным. Пустой суффикс означает ожидающий идентификатор: такой каталог проходит ту же строгую проверку, но классифицируется как неактивный и не доступен потребителям активных источников. В каждом каталоге банка единственным дочерним каталогом должен быть `categories`; он содержит один или более обычных `.json`-файлов без символических ссылок и других элементов.

Имя файла канонически вычисляется только из первого псевдонима `category`: для строки это сама строка, для массива — элемент с индексом 0. В уже проверенном NFC-псевдониме каждая максимальная последовательность пробельных Unicode-символов, управляющих символов категории `Cc` или небезопасных для Windows символов `<>:"/\|?*` заменяется одним ASCII-пробелом; регистр и вся остальная пунктуация сохраняются. Затем пробелы и точки удаляются с обоих концов. Если первая отделённая точкой часть без учёта регистра равна `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9` или `LPT1`–`LPT9`, к этой части добавляется `_` (`CON` → `CON_`, `CON.rules` → `CON_.rules`). К результату добавляется точный суффикс `.json`. Фактический basename должен в точности совпадать с результатом: для эквивалентности его не нормализуют. При изменении первого псевдонима файл необходимо переименовать; последующие псевдонимы и `unified_category` на имя не влияют.

Корень каждого файла — строгий JSON-объект с полями в порядке: обязательное `category`, необязательное `unified_category`, затем ровно один непустой селектор `include_mcc` или `exclude_mcc`. Другие поля и повторяющиеся JSON-ключи запрещены. `category` — либо одна непустая NFC-строка без пробелов по краям, либо непустой упорядоченный массив таких строк; массив не может содержать точных повторов. Это необработанные банковские псевдонимы одного правила: их переданные значения и скалярная или массивная форма сохраняются. Вычисление имени файла не изменяет псевдонимы и не влияет на сопоставление. Для каждого псевдонима вычисляется общий нормализованный ключ: NFC; удалить `[^\w\s]` Unicode-регулярным выражением; заменить `[_\s]+` одним пробелом; `strip()`; `lower()`. Ключ обязан быть непустым, поэтому псевдоним только из удаляемых знаков отклоняется. Внутри одного банка все ключи, включая элементы одного массива и разных файлов, уникальны; у разных банков ключи независимы. JSON Schema `uniqueItems` проверяет только точные структурные повторы в одном массиве и не может обеспечить непустой нормализованный ключ, нормализованную или межфайловую уникальность. JSON Schema также не может проверить связь значения в JSON с именем файла: её проверяет валидатор во время выполнения. `POST /banks/{positive-id}/categories/match` использует те же ключи поиска, не изменяя необработанные правила или возвращаемые объекты. Нормализатор не использует `unified_category` и не выполняет приближённое или MCC-сопоставление.

`unified_category`, если присутствует, остаётся одной непустой обрезанной NFC-строкой; не добавляйте, не удаляйте и не изменяйте её без публичного подтверждения и проверки сопровождающим. MCC — JSON-целые числа от 1 до 9999, не boolean и не дробные, без повторов и в строго возрастающем порядке. Существующая проверка переносимости имени и пути выполняется до чтения JSON. После проверки правила и `category` несовпадение с каноническим именем немедленно вызывает `DataError`: сообщение содержит фактический путь относительно корня репозитория, точное ожидаемое имя файла и безопасно заключённый в кавычки исходный первый псевдоним. Строгая проверка также отклоняет символические ссылки, дополнительные поля и некорректный порядок полей; первая ошибка сообщается детерминированно.

Пример `src/Пример Банк-ru_123/categories/Ж д билеты.json`:

```json
{
  "category": ["Ж/д билеты", "Поезда"],
  "unified_category": "Железнодорожные билеты",
  "include_mcc": [5812, 5814]
}
```
### Автоматическая проверка

Workflow [`Validate cashback mappings`](.github/workflows/validate.yml) автоматически запускает модульные тесты и строгую проверку всех active- и pending-источников для каждого push и pull request. Контрибьютору не нужны локальная настройка или команды проверки.

Для автоматизации сопровождающих команда `python3 scripts/cashbacks.py pending --json` печатает лексически отсортированный JSON-массив относительных путей `src/<метка>-<cc>_/categories`, не включая active-каталоги. Это не шаг процесса внесения изменений. При каждом push в `main` активный workflow [`Track pending bank IDs`](.github/workflows/track-pending-bank-bank-ids.yml) последовательно проверяет текущий `refs/heads/main` и запускает трекер GitHub issues для этих путей. Промежуточное состояние не обязано быть атомарно актуальным, и платформа может не выполнить каждый переполненный триггер; после завершения сохранившихся запусков в очереди последний из них приводит issues к текущему состоянию `main`.

Документация сервера и Docker: [server/README.md](server/README.md).

Как предложить изменение, описано в [CONTRIBUTING.md](CONTRIBUTING.md).

<a id="english"></a>
## English

### Data layout

Each bank is stored only in one `categories/` directory:

```text
src/<human-readable label>-<cc>_<bank-id>/categories/<filename-from-first-category>.json
src/<human-readable label>-<cc>_/categories/<filename-from-first-category>.json
```

`<human-readable label>` is non-empty and trimmed; `<cc>` is exactly two lowercase ASCII letters. The final `_` separates the identifier: a positive decimal `bank-id` without leading zeroes makes the bank active. An empty suffix means its ID is pending: that tree receives the same strict validation but is classified inactive and is not available to active-source consumers. Every bank directory must have `categories` as its sole child; it contains one or more ordinary `.json` files and no symlinks or other entries.

The filename is canonically projected only from the first `category` alias: the string itself for a scalar, or index 0 for an array. In that validated NFC alias, each maximal run of Unicode whitespace, `Cc` control characters, or the Windows-unsafe characters `<>:"/\|?*` is replaced with one ASCII space; case and all other punctuation are preserved. Spaces and periods are then stripped from both ends. If the case-insensitive first dot-separated component is `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, or `LPT1`–`LPT9`, `_` is appended to that component (`CON` → `CON_`, `CON.rules` → `CON_.rules`). The exact `.json` suffix is appended. The actual basename must exactly equal this result; it is never normalized for equivalence. Changing the first alias requires renaming the file; later aliases and `unified_category` do not affect its name.

Each file root is a strict JSON object whose fields are ordered as required `category`, optional `unified_category`, then exactly one non-empty selector, `include_mcc` or `exclude_mcc`. Other fields and duplicate JSON keys are forbidden. `category` is either one non-empty trimmed NFC string or a non-empty ordered array of such strings; an array contains no exact duplicates. These are raw aliases for one bank rule, and their submitted scalar-or-array shape and values are preserved. Projecting the filename does not mutate aliases or affect matching. Each alias receives one shared normalized key: NFC; remove `[^\w\s]` with a Unicode regex; collapse `[_\s]+` to one space; strip; `lower()`. The key must be non-empty, so an alias made only of removed punctuation is rejected. Within one bank, all keys—including entries in one array and separate files—are distinct; different banks have independent keys. JSON Schema `uniqueItems` checks only exact structural duplicates within one array and cannot enforce a non-empty normalized key or normalized or cross-file uniqueness. JSON Schema also cannot enforce a relation between a JSON value and its filename; the runtime validator does. `POST /banks/{positive-id}/categories/match` uses the same lookup keys without changing raw rules or returned objects. The normalizer does not use `unified_category` and performs no fuzzy or MCC-based matching.


`unified_category`, when present, remains one non-empty trimmed NFC string; do not add, remove, or change it without public evidence and maintainer review. MCCs are JSON integers from 1 through 9999, not booleans or floats, with no duplicates and strictly ascending. The existing filename and path portability checks run before JSON is read. After the rule and `category` are validated, a canonical-name mismatch immediately raises `DataError`: its message contains the actual repository-relative path, the exact expected filename, and the safely quoted raw first alias. Strict validation also rejects symlinks, extra fields, and invalid field ordering, reporting the first failure deterministically.

Example `src/Example Bank-ru_123/categories/Rail tickets.json`:

```json
{
  "category": ["Rail tickets", "Trains"],
  "unified_category": "Rail tickets",
  "include_mcc": [5812, 5814]
}
```

### Automatic validation

The [`Validate cashback mappings`](.github/workflows/validate.yml) workflow automatically runs the unit tests and strict validation of all active and pending sources for every push and pull request. Contributors need no local setup or validation commands.

For maintainer automation, `python3 scripts/cashbacks.py pending --json` prints a lexically sorted JSON array of repository-relative `src/<label>-<cc>_/categories` paths, excluding active directories. This is not a contribution step. On every push to `main`, the active [`Track pending bank IDs`](.github/workflows/track-pending-bank-bank-ids.yml) workflow serially checks out current `refs/heads/main` and runs the GitHub issue tracker for those paths. Intermediate issue state need not be atomically current, and the platform may not execute every overflow trigger; after surviving queued runs drain, the last such run converges issues to current `main`.

Server and Docker documentation: [server/README.md](server/README.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) to propose a change.
