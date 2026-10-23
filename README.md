# y2026-hmb
HMB — Harness–Model Benchmark

Бенчмарк связок «harness + модель» на практических задачах:
программирование, работа в терминале, Excel.
Дизайн и план: [docs/bench-001-proposal.md](docs/bench-001-proposal.md).

## Быстрый старт (v0, ручной режим)

Требования: Python 3.9+ (PyYAML; для excel-задачи — openpyxl).
Команды запускаются из корня репозитория.

```
python -m hmb_runner list                  # список задач
python -m hmb_runner sanity --all          # verifier'ы обязаны проходить эталонные решения
python -m hmb_runner start <task-id>       # развернёт workspaces/<таймстемп>-<task-id>,
                                           # снимет версии, напечатает промпт для панели
# ... открыть workspace в VSCode, вставить промпт в панель Claude Code, дождаться завершения ...
python -m hmb_runner verify <task-id>      # проверит свежайший workspace задачи и допишет
                                           # результат в results/journal.jsonl
```

Каждый `start` создаёт отдельный воркспейс `workspaces/<ГГГГММДДТЧЧММСС>-<task-id>`
(локальное время, точность до секунды), так что повторные прогоны не затирают друг друга.
`verify` без `--workspace` берёт самый свежий воркспейс задачи.
Полезные флаги: `verify --workspace <dir>`,
`verify --notes "..." --model <model>` (метаданные прогона), `verify --no-record`
(проверить без записи). Снимок версий расширения/VSCode/CLI делается автоматически
в `start` и попадает в запись журнала. Кроме журнала, `verify` формирует excel-отчёт
`results/report-<ГГГГММДДТЧЧММСС>-<task-id>.xlsx`: лист «Статистика» (score, время,
модель, версии и т.п.) и лист «Ошибки» со списком проваленных проверок
(нужен openpyxl).

## Структура

- `tasks/<category>/<task-id>/` — `task.yaml` (промпт, настройки), `env/` (стартовый
  workspace), `verify/` (автопроверка, печатает `SCORE: <доля>`; может дописывать
  детальные кейсы одной jsonl-строкой в файл из `$HMB_DETAILS` — попадают в лист
  «Кейсы» excel-отчёта), `solution/` (эталон для `sanity`), `tools/` (генераторы
  данных с фиксированным сидом)
- `hmb_runner/` — CLI ручного прогона: `list / start / verify / sanity / snapshot`
- `suites/core.yaml` — набор задач v0 (по одной на категорию)
- `results/`, `workspaces/` — журнал прогонов и рабочие папки (в git не входят)
