# OnlineData

Сырые выгрузки лежат в `raw/`. Рабочая история — канонические таблицы `ext_tennis_canonical_matches` и `ext_tennis_canonical_players`, плюс выгрузка в `canonical/`.

Файлы Jeff Sackmann, Tennis Abstract (`tml`) и Match Charting Project распространяются под CC BY-NC-SA 4.0. Их можно использовать только некоммерчески и с указанием автора.

| Каталог | Что внутри |
| --- | --- |
| `raw/ten` | Сезонные файлы с коэффициентами и статистикой подачи |
| `raw/odds` | `atp_tennis.csv` с линией. `atp_matches_2000.csv` повторяет Sackmann и в базу не грузится |
| `raw/tml` | Годовые файлы Tennis Abstract, включая квалификацию |
| `raw/sackmann` | CSV Jeff Sackmann. Матчи повторяют sqlite и в базу не грузятся. Из этой папки берётся только продолжение рейтинга |
| `raw/sqlite` | Общий sqlite-дамп Sackmann: матчи, игроки, рейтинги |
| `raw/charting` | Подачи Match Charting Project |
| `canonical` | `matches.csv.gz`, `players.csv.gz`, `manifest.json` после склейки |

`scripts/import_external_tennis.py` дописывает сырые строки и не удаляет уже загруженное. `scripts/build_canonical_tennis.py` собирает одну строку на матч. Повтор TML и sqlite больше не двигает рейтинг дважды.
