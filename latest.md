# Текущий статус Neurobet

## Полный аудит теннисного doubles архива — 2026-11-01

### Завершено

- Выполнен полный checkpointed census замороженной когорты: **1 213/1 213** завершённых числовых Fonbet doubles событий, **237 946** point-history документов и **238 094** версии истории. Все пакеты объединены после завершения прохода.
- Snapshot index уменьшен с 500 до 100 строк после подтверждённого statement timeout; cutoff и frozen event digest не менялись. Полный отчёт: [`report.json`](docs/research/tennis_doubles_full_census_2026-11-01/report.json), manifest: [`training_manifest.json`](docs/research/tennis_doubles_full_census_2026-11-01/training_manifest.json), разбор: [`summary.md`](docs/research/tennis_doubles_full_census_2026-11-01/summary.md).
- Dataset digest: `ea79ee61d995f5b2cdaf0c86b322e8f302e1e4f14efbb01eae27b39d4fc643da`. Feature PIT certification: **122 842/122 842** примеров.

### Результат по целям

| Цель | Матчи с меткой | Примеры | Причинный train | Validation (4 окна) | Holdout |
|---|---:|---:|---:|---:|---:|
| Match | 1 203 | 1 203 | 544 | 96 / 96 / 97 / 96 | 241 |
| Set | 1 201 | 121 639 | 544 | 96 / 96 / 97 / 96 | 241 |
| Game | 0 | 0 | 0 | 0 / 0 / 0 / 0 | 0 |
| Point | 0 | 0 | 0 | 0 / 0 / 0 / 0 | 0 |

### Что блокирует допуск

- Порог каждого validation-окна — **100 уникальных матчей**; для match/set не хватает 3–4 матчей в каждом окне.
- Point/game не имеют пригодных меток в этой когорте.
- Для двух исходов нет подтверждённого времени доступности; manifest result-availability certification не пройден.
- Doubles feature contract и manifest требуют отдельной сертификации.

Допуск остаётся `blocked`; пороги не снижались, качество holdout не открывалось, модели не обучались. Инженерная завершённость аудита не означает готовность данных к обучению. Следующие шаги: получить доказанные point/game labels и времена двух результатов, сертифицировать doubles contract/manifest, повторить audit; отдельно закрыть приёмку новых событий и 24-часовое наблюдение.
