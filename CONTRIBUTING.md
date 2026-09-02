# Как внести изменения / Contributing

[Русский](#russian) | [English](#english)

<a id="russian"></a>
## Русский

Спасибо за помощь в поддержке правил кешбэка. Изменение должно быть небольшим, проверяемым и подтверждённым публичным источником банка.

### Через веб-интерфейс GitHub

Для простого изменения локальная настройка не нужна:

1. Откройте нужный файл `src/<метка>-<cc>_<bank-id>/categories/<имя-по-первой-категории>.json` и нажмите кнопку редактирования. Для новой категории создайте отдельный файл в этом каталоге.
2. Для нового активного банка используйте `src/<метка>-<cc>_<bank-id>/categories/`; для банка с ещё неизвестным ID — `src/<метка>-<cc>_/categories/`. Метка непуста и без пробелов по краям; `<cc>` состоит ровно из двух строчных ASCII-букв; положительный ID не имеет ведущих нулей. Пустой суффикс означает pending-банк: он строго проверяется, но неактивен.
3. При добавлении или переименовании банка приложите публичное подтверждение идентичности банка и выбранного суффикса страны. Не выводите страну из названия банка. Для завершённой миграции 15 банков доказательство получено из `https://github.com/zenmoney/sms-formats` на закреплённом commit `eba469b84fe51e44c5e3c922fd3191addad779e6`; это одноразовый источник миграции, а не реестр.
4. Изменяйте только нужные файлы категорий этого банка. Нажмите **Propose changes** и создайте pull request. Приложите ссылки на публичные страницы или документы банка; не публикуйте личные данные, переписку или закрытые материалы.

### Формат исходника

В каждом каталоге банка единственным дочерним каталогом является `categories`. Он содержит один или более обычных `.json`-файлов без символических ссылок, вложенных путей и неожиданных элементов. Имя файла канонически вычисляется только из первого псевдонима `category`: для строки это сама строка, для массива — элемент с индексом 0. В уже проверенном NFC-псевдониме каждая максимальная последовательность пробельных Unicode-символов, управляющих символов категории `Cc` или небезопасных для Windows символов `<>:"/\|?*` заменяется одним ASCII-пробелом; регистр и вся остальная пунктуация сохраняются. Затем пробелы и точки удаляются с обоих концов. Если первая отделённая точкой часть без учёта регистра равна `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9` или `LPT1`–`LPT9`, к этой части добавляется `_` (`CON` → `CON_`, `CON.rules` → `CON_.rules`). К результату добавляется точный суффикс `.json`. Фактический basename должен в точности совпадать с результатом: для эквивалентности его не нормализуют. При изменении первого псевдонима переименуйте файл; последующие псевдонимы и `unified_category` на имя не влияют.

Корень каждого файла — строгий JSON-объект с полями в каноническом порядке:

- обязательное `category` — одна непустая обрезанная NFC-строка или непустой упорядоченный массив таких строк;
- необязательное `unified_category` — одна непустая обрезанная NFC-строка;
- ровно один селектор, `include_mcc` или `exclude_mcc` — непустой массив MCC-кодов.

Псевдонимы `category` описывают одно правило; их переданные значения и скалярная или массивная форма сохраняются. Вычисление имени файла не изменяет псевдонимы и не влияет на сопоставление. Для каждого псевдонима вычисляется нормализованный ключ: NFC; удалить `[^\w\s]` Unicode-регулярным выражением; заменить `[_\s]+` одним пробелом; `strip()`; `lower()`. Ключ обязан быть непустым, поэтому псевдоним только из удаляемых знаков отклоняется. Внутри одного банка все ключи, включая элементы одного массива и разных файлов, уникальны; у разных банков ключи независимы. JSON Schema `uniqueItems` проверяет только точные структурные повторы в одном массиве и не может обеспечить непустой нормализованный ключ, нормализованную или межфайловую уникальность. JSON Schema также не может проверить связь значения в JSON с именем файла: её проверяет валидатор во время выполнения. `POST /banks/{positive-id}/categories/match` использует те же ключи поиска, не изменяя необработанные правила или возвращаемые объекты. Нормализатор не использует `unified_category` и не выполняет приближённое или MCC-сопоставление.

`unified_category` имеет самостоятельный смысл: не добавляйте, не удаляйте и не изменяйте его без публичного подтверждения и проверки сопровождающим. Другие поля и повторяющиеся JSON-ключи запрещены.

MCC — JSON-целые числа от 1 до 9999. `exclude_mcc` можно использовать вместо `include_mcc`, но не вместе с ним. Существующая проверка переносимости имени и пути выполняется до чтения JSON. После проверки правила и `category` несовпадение с каноническим именем немедленно вызывает `DataError`: сообщение содержит фактический путь относительно корня репозитория, точное ожидаемое имя файла и безопасно заключённый в кавычки исходный первый псевдоним. Строгая проверка также отклоняет символические ссылки, неизвестные поля, неверный порядок, некорректные псевдонимы и MCC; первая ошибка сообщается детерминированно.

Пример `src/Пример Банк-ru_123/categories/Ж д билеты.json`:

```json
{
  "category": ["Ж/д билеты", "Поезда"],
  "unified_category": "Железнодорожные билеты",
  "include_mcc": [1234, 5678]
}
```

### Автоматическая проверка

После открытия pull request workflow [`Validate cashback mappings`](.github/workflows/validate.yml) автоматически запускает модульные тесты и строгую проверку всех active- и pending-источников. Контрибьютору не нужно устанавливать Python или запускать локальные команды проверки.

### Issues для ожидающих идентификаторов банков

Точная репозиторная метка `pending-bank-identity` задаёт границу полномочий трекера, а скрытый маркер `<!-- pending-bank-identity:path=<encoded-path> -->` задаёт идентичность пути. Трекер постранично, по 100, получает только открытые issues с этой меткой и исключает pull request. В issue есть упоминание `@${GITHUB_REPOSITORY_OWNER}`, но владельца репозитория не назначают: новый issue создаётся без assignee. Для ожидающего пути обновляется только основной issue с наименьшим номером, сохраняя назначение человека; дубликаты остаются, пока путь ожидает идентификатор. Когда путь отсутствует, закрываются все issues с одним каноническим маркером этого пути. Сопровождающий проверяет ID банка и переименовывает каталог в active-форму. Удалённая или переименованная метка репозитория — административная ошибка; трекер не делает поиск для восстановления.

<a id="english"></a>
## English

Thank you for helping maintain the cashback rules. A change should be focused, verifiable, and supported by public evidence from the bank.

### Using the GitHub web editor

A simple change needs no local setup:

1. Open the relevant `src/<label>-<cc>_<bank-id>/categories/<filename-from-first-category>.json` file and click the edit button. Create a separate file in that directory for a new category.
2. For a new active bank, use `src/<label>-<cc>_<positive-canonical-id>/categories/`; for a bank whose ID is not known yet, use `src/<label>-<cc>_/categories/`. The label is non-empty and trimmed; `<cc>` is exactly two lowercase ASCII letters; a positive ID has no leading zeroes. The empty suffix means the bank is pending: it is strictly validated but inactive.
3. When adding or renaming a bank, provide public evidence of the bank's identity and the chosen country suffix. Do not infer the country from the bank label. The completed 15-bank migration obtained its evidence from `https://github.com/zenmoney/sms-formats` at pinned commit `eba469b84fe51e44c5e3c922fd3191addad779e6`; that is a one-time migration source, not a registry.
4. Edit only the relevant category files for that bank. Click **Propose changes** and open a pull request. Link public bank pages or documents; do not publish personal data, private correspondence, or restricted material.

### Source format

`categories` is the sole child directory of every bank directory. It contains one or more ordinary `.json` files with no symlinks, nested paths, or unexpected entries. The filename is canonically projected only from the first `category` alias: the string itself for a scalar, or index 0 for an array. In that validated NFC alias, each maximal run of Unicode whitespace, `Cc` control characters, or the Windows-unsafe characters `<>:"/\|?*` is replaced with one ASCII space; case and all other punctuation are preserved. Spaces and periods are then stripped from both ends. If the case-insensitive first dot-separated component is `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, or `LPT1`–`LPT9`, `_` is appended to that component (`CON` → `CON_`, `CON.rules` → `CON_.rules`). The exact `.json` suffix is appended. The actual basename must exactly equal this result; it is never normalized for equivalence. When the first alias changes, rename the file; later aliases and `unified_category` do not affect its name.

Each file root is a strict JSON object with fields in canonical order:

- required `category`: one non-empty trimmed NFC string or a non-empty ordered array of such strings;
- optional `unified_category`: one non-empty trimmed NFC string;
- exactly one selector, `include_mcc` or `exclude_mcc`: a non-empty MCC array.

`category` aliases describe one rule. Their submitted scalar-or-array shape and raw values are preserved. Projecting the filename does not mutate aliases or affect matching. Each alias receives a normalized key: NFC; remove `[^\w\s]` with a Unicode regex; collapse `[_\s]+` to one space; strip; `lower()`. The key must be non-empty, so an alias made only of removed punctuation is rejected. Within one bank, all keys—including entries in one array and separate files—are distinct; different banks have independent keys. JSON Schema `uniqueItems` checks only exact structural duplicates within one array and cannot enforce a non-empty normalized key or normalized or cross-file uniqueness. JSON Schema also cannot enforce a relation between a JSON value and its filename; the runtime validator does. `POST /banks/{positive-id}/categories/match` uses the same lookup keys without changing raw rules or returned objects. The normalizer does not use `unified_category` and performs no fuzzy or MCC-based matching.

`unified_category` has independent meaning: do not add, remove, or change it without public evidence and maintainer review. Other fields and duplicate JSON keys are forbidden.

MCCs are JSON integers from 1 through 9999. `exclude_mcc` may replace `include_mcc`, but the two cannot appear together. The existing filename and path portability checks run before JSON is read. After the rule and `category` are validated, a canonical-name mismatch immediately raises `DataError`: its message contains the actual repository-relative path, the exact expected filename, and the safely quoted raw first alias. Strict validation also rejects symlinks, unknown fields, invalid ordering, invalid aliases, and invalid MCCs, reporting the first failure deterministically.

Example `src/Example Bank-ru_123/categories/Rail tickets.json`:

```json
{
  "category": ["Rail tickets", "Trains"],
  "unified_category": "Rail tickets",
  "include_mcc": [1234, 5678]
}
```

### Automatic validation

After a pull request is opened, the [`Validate cashback mappings`](.github/workflows/validate.yml) workflow automatically runs the unit tests and strict validation of all active and pending sources. Contributors do not need to install Python or run local validation commands.

### Pending bank identity issues

The exact repository label `pending-bank-identity` is the tracker’s authority boundary, and the hidden marker `<!-- pending-bank-identity:path=<encoded-path> -->` identifies the path. The tracker paginates 100 per page only through open issues filtered by that label and excludes pull requests. An issue includes `@${GITHUB_REPOSITORY_OWNER}` without assigning the repository owner: a new issue is created unassigned. For a pending path, only the lowest-numbered primary issue is updated and a human assignee is preserved; duplicates remain while the path is pending. When a path is absent, every issue with its single canonical marker closes. A maintainer verifies the bank ID and renames the directory to active form. A removed or renamed repository label is an administrative error; the tracker performs no recovery scan.
