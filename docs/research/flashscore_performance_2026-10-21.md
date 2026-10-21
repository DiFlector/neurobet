# Flashscore: исправления производительности и runtime-проверка — 2026-10-21

## Наблюдавшиеся дефекты

Перед исправлением конкурентный импорт реальных профилей завершился `UniqueViolation` на `uq_ext_tennis_matches_source` для Flashscore match ID `jJI2NEVO`. Два профиля могли одновременно пройти `SELECT` отсутствующей строки и оба вставить один результат; ошибка откатывала транзакцию целого профиля. В том же часовом фрагменте один импорт распарсил 42 результата, а повторные импортные попытки давали лишнюю работу.

Сбор PBP также делал локальную паузу между сетами поверх глобального source regulator. При управляемом worker это добавляло задержку, не снижая частоту запросов ниже уже обеспечиваемого общего лимита. Журналы Research содержали локальное время контейнера UTC+3 с буквальным суффиксом `UTC`, что мешало сопоставлять операции и timestamps БД.

После фиксации теннисного URL поиск всё ещё периодически не открывал поисковое поле: `button.searchIcon` был видим, но UI не монтировал диалог; накопилось 36 legacy `SEARCH_INPUT_MISSING`. Это делало profile lookup зависимым от нестабильной гидратации и запускало Chromium на короткую JSON-поисковую задачу.

## Исправления

- Импорты сериализуют пересекающиеся Flashscore player/match IDs через сортируемые transaction advisory locks. Ключевой блок действует до commit/rollback и закрывает check-then-insert гонку между разными профилями и исполнителями.
- Внешние игроки, текущие матчевые проекции и последние observation версии считываются пакетно; observation query использует `DISTINCT ON (source_match_id)` и не загружает все старые версии каждой строки. При одинаковой странице вместо двух проекционных запросов на матч выполняются три пакетных чтения на страницу.
- Между вкладками сетов дополнительный sleep оставлен только автономному fetch без общего `source_access` regulator.
- Formatter Research принудительно использует `time.gmtime`, независимо от `TZ=Europe/Moscow`.
- После следующего запуска очереди логи выявили ещё одну причину пустых поисков: generic `https://www.flashscore.com/` открыл футбольную главную. Поиск профиля и Referer переведены на `https://www.flashscore.com/tennis/`.
- Profile lookup переведён на read-only `GET https://s.livesport.services/api/v2/search/?q=...`, endpoint был наблюдён в сетевом контуре обычного tennis search и возвращает тот же JSON. Запрос проходит явную проверку DomainFilter для `livesport.services`, общий source slot и tennis Referer. Парсер оставляет только Tennis/Player, сохраняет bounded-варианты имени и fail-closed причины 403/429, timeout, HTTP и JSON parse. Запуск Chromium на этом этапе больше не нужен.

## Проверки

- Тесты Flashscore и восстановления очереди: **40 passed**.
- Два конкурентных транзакционных writer для одного нового source match ID, выполняющих тот же advisory-lock helper, получили исходы `inserted` и `existing`; в БД была ровно одна строка, тестовую строку удалили.
- Runtime formatter выдал `2026-10-21T05:06:28 UTC`, совпадающее с UTC временем контейнера; `logging.Formatter.converter is time.gmtime`.
- После выкладки Research, `flashscore-worker` и proxy relay стали `healthy`; source slots показывали `1/2`, cooldown отсутствовал. В worker логах после последнего перезапуска ошибок `UniqueViolation` не появилось.
- После исправления профильного URL и Referer `resolve_player_profile("Arantxa Rus")`, выполненный внутри общего durable source slot, вернул единственного ожидаемого кандидата `Rus Arantxa` (`84GyAaXB`). Напротив, за тот же runtime фрагмент worker сообщил `SEARCH_INPUT_MISSING` уже на tennis-странице; smoke подтверждает исправление неверного спортивного маршрута, но не стабильность поискового UI для всего пула задач.
- Flashscore/confirmation regression suite после сборки: **40 passed**. Образы `research` и `flashscore-worker` пересобраны; после выкладки оба стали healthy.
- После перехода с UI на observed JSON endpoint повторный контрольный поиск через общий source slot вернул `Rus Arantxa` (`84GyAaXB`) за **2.18 с**, включая получение слота. Это одиночный end-to-end sample, не paired benchmark; сравнительный коэффициент ускорения не заявляется. Регрессия поиска/очереди после изменения: **44 passed**. В 11 player jobs, обновлённых после 05:24 UTC, нет `SEARCH_INPUT_MISSING`; три получили подтверждённый пустой ответ `SEARCH_COMPLETED_NO_MATCH`, два — `REFRESH_ABORTED`.

## Текущая граница измерения

Это проверка исправления гонки, маршрута поиска и сокращения DB round trips, а не полный paired throughput benchmark. За короткое окно после выкладки ещё не завершился достаточно сопоставимый повторный импорт профиля, поэтому не утверждается улучшение elapsed time, Chromium launches или полное снижение сетевых запросов. После деплоя прямого endpoint на 05:24 UTC 22 подходящих события; число подготовленных пар всё ещё низкое, поэтому нужно продолжать наблюдение и разбирать `SEARCH_COMPLETED_NO_MATCH`/`REFRESH_ABORTED` отдельно.

Для закрытия Phase 49 нужен минимум один и тот же сохранённый набор профилей/матчей, прогнанный до и после с подсчётом elapsed time, сетевых операций, запусков Chromium и объёма разбора; затем нужен 30-минутный live runtime soak и проверка, что архив не ухудшает задержку live PBP.

## Пул HTTP-соединений поиска профиля — 07:56 UTC

Профильный поиск переведён с нового `httpx.Client` на каждый job на один потокобезопасный process-level keep-alive client, с `max_connections=2`, `max_keepalive_connections=2`, 30-секундным сроком idle keep-alive. Глобальный durable source slot по-прежнему берётся отдельно на каждый GET; правила поиска, заголовки и обработка блокировок не изменены. `atexit` закрывает пул.

Парный замер выполнен из Research против одного и того же endpoint/query (`Arantxa Rus`), 4 холодных и 4 pooled GET в чередующемся порядке. Каждый сетевой запрос получил participant source slot; ожидание слота не включено в latency. Все 8 ответов — HTTP 200 с JSON list. Медиана cold — 0.21665 с, pooled — 0.07025 с, снижение **67.6%** для request/client path. Полный JSON: `docs/research/flashscore_http_pool_benchmark_2026-10-21.json`. Выборка мала и покрывает один query: результат подтверждает сокращение latency повторного GET, но не общий throughput, длительность загрузки профиля или PBP.

После сборки Research и `flashscore-worker` оба контейнера healthy. Полный Research suite вместе с теннисным sport parser: **109 passed**. Полный paired benchmark импорта/PBP и 30-минутный soak остаются открытыми.

## Дополнительная оптимизация расписания backfill — 05:35 UTC

Worker logs показали, что `dispatch_tennis_archive_backfill` перечитывал 2,256 завершённых событий каждые 15 секунд вместе с live discovery, хотя его работа ограничена низким приоритетом и durable очередь уже хранит найденные задания. Перенёс сканирование в отдельный таймер `FLASHSCORE_ARCHIVE_DISCOVERY_SECONDS` (default 900 секунд); live discovery/PBP остался на 15 секундах. Это снижает частоту полного исторического скана с 240 до 4 раз в час (60× по конфигурации), без изменения общего числа сетевых слотов или приоритетов.

После пересборки `flashscore-worker` и proxy relay оба стали healthy, heartbeat обновлялся, source regulator показывал 2/2 слота. В 20-секундном наблюдении после стартового backfill был один проход с `scanned=2256`, затем live discovery продолжился на 05:35:42 и PBP dispatcher выдал два задания; повторного архивного скана за это окно не было. Это подтверждает разделение таймеров, но не является полным 30-минутным soak или paired throughput benchmark. Очередь на 05:35 показывала 22 подходящих события, 1 prepared player, 44 `waiting_players`, 48 queued tasks, 11 документов и 11 подтверждённых связей за последний час; узкое место идентификации игроков сохраняется.

## Rate limit на каждый поисковый GET — 05:39 UTC

Дополнительно выяснилось, что общий source slot удерживался вокруг всего поиска профиля, хотя один поиск может выполнять до трёх HTTP GET для вариантов имени. Перенёс выдачу слота внутрь цикла запросов: теперь каждый фактический GET проходит через глобальный регулятор и разделён минимум двумя секундами, сохраняя общий предел двух одновременных операций. Тест проверяет три выдачи слота для трёх вариантов; после сборки Research и `flashscore-worker` Flashscore parser/search — **41 passed**, confirmation recovery — **4 passed**, общий Research suite — passed, контейнеры healthy. Live runtime lookup `Arantxa Rus` вернул `Rus Arantxa` (`84GyAaXB`) за **0.52 с**. Это один короткий кандидат-поиск, не paired throughput benchmark.

## Дополнительная ошибка и исправление — 05:50 UTC

Первый попытанный 30-минутный soak прерван как неуспешный: в 05:50 worker залогировал `DetachedInstanceError` при подготовке участника после истечения ORM-объекта между сессиями; рядом был отдельный таймаут загрузки профиля. Исправление сохраняет canonical participant name как строковое значение внутри исходной сессии перед записью confirmation evidence в новой сессии. Оно не меняет правило сопоставления или evidence заявки.

Образы `research` и `flashscore-worker` пересобраны и выкачены; оба healthy. Целевые parser/confirmation тесты прошли: **45 passed**; общий `make test-research` также прошёл. Новый 30-минутный soak завершён в 06:23 UTC: 31/31 queue API samples успешны, worker healthy 31/31, PBP overdue 0 во всех точках. Число подходящих событий колебалось 22–26; счётчик документов и подтверждённых связей за последний час увеличился 15→29. Полный лог находится в `docs/research/flashscore_soak_2026-10-21_postfix.jsonl`.

Во время soak две попытки PBP для двух разных событий завершились retry после внешнего `Page.goto` timeout 25 секунд. Playwright также напечатал callback `CancelledError`; несколько traceback-строк относятся к одному timeout и не являются независимыми сбоями. Ещё один такой timeout произошёл в 06:23:08, сразу после последнего API sample. Слоты освобождались, просроченных PBP не возникло. `DetachedInstanceError` после фикса не повторялся. Сервисный runtime gate принят; время ответа Flashscore и полнота отдельных попыток остаются ограничением. Новый аудит в 06:23 показывает 20 внешних матчей и 79 версий документов; количество уникальных матчей с целью меньше и всё ещё блокирует обучение.

## Переиспользование Chromium для PBP — 06:56 UTC

Синхронный PBP fetcher и live-discovery переведены на thread-local Chromium pool. Процесс браузера общий для последовательных задач одного worker-потока; каждый job получает новый browser context, который закрывается после работы. Браузер ротируется после 50 задач, 15 минут или ошибки. Отловленный timeout перехода к вкладке помечает браузер на завершение после job. Глобальный source regulator и число PBP workers не менялись.

Offline smoke в `flashscore-worker` подтвердил одно и то же Chromium root process при двух отдельных context/job: число процессов изменилось `2 → 3 → 3`, идентичность browser осталась той же. Это подтверждает повторное использование и изоляцию жизненного цикла, но не меряет сетевой путь.

Микробенчмарк трёх запусков на `data:` странице: свежий Playwright+Chromium+context медиана **0.762 с**; первый pooled job — **0.594 с**, два тёплых context — **0.094 с** медианно, то есть на **87.6%** меньше времени подготовки одного тёплого job. Это локальный browser-startup benchmark без Flashscore HTTP/рендеринга и не заменяет парный benchmark реальных документов; ожидаемое end-to-end ускорение ниже и пока не измерено.

Regression suite после пула: **13 passed** (pool lifecycle/invalidation/age rotation + PBP parser); `make test-research` passed. После первой сборки `research`/`flashscore-worker` обе службы стали healthy. После добавления обработки handled navigation timeout образ нужно пересобрать и проверить отдельно.

Новый аудит рабочей БД завершён в `2026-10-21T06:56:25Z`: gate всё ещё `blocked`; очковые/геймовские/сетовые метки имеют по 17 уникальных матчей, матчевых меток 2,140, но `feature PIT=0/2,846`. Общий manifest 1,848 событий (1,478 development / 370 holdout). Все четыре temporal precedence folds провалены; P/G/S train 3, validation 1/1/2/1, holdout 2. Артефакт — `tennis_label_audit_2026-10-21_postbrowserpool.json`.

## Runtime после развёртывания browser pool — 06:59 UTC

После пересборки с обработкой отловленных navigation timeout все затронутые контейнеры healthy. Queue API ответил HTTP 200 за 0.31 с: 30 подходящих событий, 5 подготовленных участников, 56 queued, 58 waiting_players, 2 waiting_confirmation; за rolling hour показаны 69 сохранённых документов и 45 подтверждённых связей. Worker heartbeat свежий, cooldown отсутствует. Это одиночный snapshot, не soak; доля подготовленных игроков и live-vs-archive задержка остаются открытыми.

## Парсерный paired benchmark и прямой PBP маршрут — 10:24 UTC

`benchmark_tennis_point_parser.py` прогнал две версии парсера на одном offline-корпусе: 30 уникальных архивных матчей, 32,830,885 байт HTML, 8 проходов каждого парсера, по 240 сравнений событий; сетевые запросы и записи БД отсутствовали. Код baseline взят из Git `9ec3b4af117b16be7198afcf1df7732514f6756c` (`flashscore-point-history-v2`), текущий — `flashscore-point-history-v4`. Медиана baseline 1.232 с/проход, v4 — 1.194 с, то есть **3.2%** меньше времени на этот корпус (25.12 против 24.35 документов/с). Количество событий совпало в 232/240 сравнений; точная сигнатура — в 120/240. Отличия включают известные исправления стороны подачи и терминальных состояний, поэтому это не заявляется как строгая parity-эквивалентность; источник сверяется отдельным DOM-review. Результат показывает, что чистое HTML-разбирание не является основным тормозом.

Для сокращения сетевых переходов добавлен direct-index путь: когда URL содержит подтверждённый Flashscore match ID, worker сразу открывает `/summary/point-by-point/`, пропуская summary navigation. Если источник отдаёт 404 или index shell без set tabs, остаётся summary-first fallback. Прямой `httpx` GET не годится вместо браузера: на одной странице HTTP вернул TLS EOF; Playwright `context.request.get` получил 200/около 0.91 MB, но raw response не содержал `matchHistoryRow` или очковых строк. DOM-путь остаётся обязательным для PBP.

Read-only source-slot пилот на том же подтверждённом матче `WAlDxZFD` вернул полные set 1 и 2 обоими вариантами. Direct-index: 3 навигации и 2 документа, два прохода заняли 13.527 и 30.598 с. Summary-first baseline: 4 навигации и те же 2 сета за 36.436 с (по переходам 5.215, 14.017, 3.626 и 9.649 с). Время сильно менялось на одном матче; обобщённое end-to-end ускорение не заявляется. Проверяемый выигрыш — одна Flashscore-навигация и один source-slot acquisition меньше на каждом матче. Подробный JSON — `docs/research/flashscore_pbp_route_benchmark_2026-10-21.json`; parser JSON — `docs/research/tennis_point_parser_benchmark_2026-10-21.json`.

`research` и `flashscore-worker` пересобраны и перезапущены в 10:20 UTC. После выкладки оба сервиса, backend и proxy relay были healthy; worker возобновил discovery. Регрессии маршрута: **68 passed**. Полный benchmark Phase 49 всё ещё открыт: нужна большая выборка однотипных профилей/матчей до и после, число сетевых операций и запусков Chromium, а также отдельное измерение live latency при архивном фоне.

## Проверка аллокаций PBP parser — 12:31 UTC

Проверена гипотеза об ускорении Python-дерева HTML: сократить сохраняемые атрибуты, использовать `__slots__` и откладывать склейку text callbacks. Сравнение проводилось на тех же 30 сохранённых матчах (32,832,939 байт), 8 проходов, без сети и записей БД. До изменения SHA-256 parser source `967c4998a6a69dc825849a9ca71cb59cc4fd6be840b0062452b17eb7cbb812c5`; candidate дал **-4.1%** throughput (медиана 1.323 с → 1.379 с на corpus pass), при этом exact event signature совпала на 240/240 документов. Изменение отменено: production source оставлен без этой оптимизации. Полный прогон сохранён в [`tennis_point_parser_optimized_benchmark_2026-10-21.json`](tennis_point_parser_optimized_benchmark_2026-10-21.json). Этот результат подтверждает, что простое сокращение Python-аллокаций не ускоряет текущую реализацию; приоритет остаётся у сокращения навигаций и browser startup, а общий paired throughput benchmark ещё не закрыт.

## Ускорение PBP HTML parser через lxml — 12:35 UTC

Профиль показал, что около 82% parse-only времени уходило в `HTMLParser.feed/goahead` и построение Python-объектов. Добавлен lxml backend с теми же правилами разбора; стандартный parser оставлен fallback. Parser version повышена до `flashscore-point-history-v5`; `lxml>=5.2,<7` добавлен в зависимости Research. Neural может использовать проверенный fallback без lxml, сохраняя те же семантические правила.

Парный прогон v4/v5 на 30 одинаковых сохранённых матчах (32,805,789 байт), 8 чередующихся проходов, сеть и DB writes выключены: медиана v4 **1.160 с**, v5 **0.327 с** на корпус из 30 документов; **+254.6% throughput**, 240/240 документов совпали по точной сигнатуре событий. Полный JSON — [`tennis_point_parser_lxml_benchmark_2026-10-21.json`](tennis_point_parser_lxml_benchmark_2026-10-21.json). Research/PBP tests — **62 passed** с lxml; спортивные parser tests — **11 passed** в fallback-режиме без lxml. Это ускоряет только локальный HTML parsing; суммарное время загрузки PBP остаётся в основном сетевым и требует отдельного paired runtime benchmark.

### Runtime deployment — 12:47 UTC

Пересобраны и пересозданы `research` и `flashscore-worker`; `research-proxy-relay` также восстановлен в healthy состоянии. `neural` не пересоздавался. Оба Flashscore-потребителя сообщают lxml 6.1.3, parser `flashscore-point-history-v5`; SHA-256 установленного parser module совпал с исходником (`5e6c637c2e9b0b1fd9d4ba21132e9d42d98cec3b28db50ef58b4d17f948cb747`). После пересборки повторены parser/research/PBP source-review suites: **62 passed in 1.01s**. Compose config и `git diff --check` прошли. Это подтверждает локальную скорость и семантический parity; throughput Flashscore по сети и влияние архивной очереди на live остаются открыты.

## Ограничение времени инкрементального refresh — 16:22 UTC

Runtime очередь показала повторяющиеся 300-секундные timeouts на обновлениях уже известных профилей (например, Gauff, Medvedev и Munk-Mortensen), из-за которых занят shared Chromium capacity, хотя при сетевой ошибке старые participant metrics остаются пригодны для чтения. Теперь известные профили с 30-дневным overlap используют отдельный deadline по умолчанию 120 секунд (`FLASHSCORE_TENNIS_INCREMENTAL_TIMEOUT_SECONDS`); первичная история по-прежнему ограничена 300 секундами (`FLASHSCORE_TENNIS_PROFILE_TIMEOUT_SECONDS`). При timeout child process завершается, lease-fencing и retry остаются прежними.

54 Research/profile/worker/lease regressions passed. Research и `flashscore-worker` пересобраны 16:22 UTC и healthy; через 14 секунд worker возобновил discovery/dispatch, API показал overdue PBP 0, 4 PBP-документа и 8 подтверждённых связей за rolling hour. Это только стартовый snapshot: ускорение, timeout rate и PBP latency пока не сравнивались. Требуется post-change soak с теми же метриками и контроль, что полный первичный импорт не теряет полноту.

## 30-минутный soak после ограничения инкрементального refresh — 16:23–16:53 UTC

Артефакты: [`flashscore_soak_2026-10-21_incremental-deadline.jsonl`](flashscore_soak_2026-10-21_incremental-deadline.jsonl) и [`flashscore_resources_2026-10-21_incremental-deadline.jsonl`](flashscore_resources_2026-10-21_incremental-deadline.jsonl). Все 60/60 запросов очереди завершились HTTP 200, worker был healthy 60/60, cooldown не возникал. API latency: p50 0.558 с, p95 0.977 с, максимум 2.554 с. Суммарно наблюдалось не более двух Chromium roots; worker достиг 1,090.6 MiB из 2 GiB и 146.22% CPU, Research — 181.3 MiB из 1.5 GiB. Доступных матчей было 27–31, подготовленных участников 8–15.

Проверка подтверждает стабильность сервиса и предел браузеров, но не своевременность всей PBP-очереди: runnable `overdue_pbp` был ненулевым в 24/60 срезов, максимум 2; в последнем срезе он равен 0. За rolling hour в последних точках сохранялось 3–4 документа и 4–5 подтверждённых связей. Старейшее PBP-ожидание и скорость продвижения полного архивного хвоста остаются отдельным эксплуатационным блокером; этот soak не является доказательством полноты импорта или обучения.

## PBP queue soak по текущему окну — 20:15–20:45 UTC

Фильтр `kind=pbp`, 61 последовательный запрос с интервалом 30 секунд. Все ответы HTTP 200, worker healthy на 61/61 выборке; latency p50/p95/max — 0.127/0.375/1.44 с. `overdue_pbp` был ненулевым в 19/61 выборке, максимум 1. PBP jobs в `waiting_players` сократились с 10 до 8, максимальный wait за окно — 8,292 с; причина старейших ожиданий `PLAYER_SOURCE_IDS_MISSING`. Число подходящих событий менялось 11→9, подготовленных игроков 6→5. Следовательно, этот замер подтверждает здоровье API/исполнителя, но не закрывает своевременность PBP: главный наблюдавшийся блокер — отсутствие подтверждённых Flashscore ID игроков, а не занятые сетевые слоты. Полный ряд — `flashscore_soak_2026-10-21_pbp-priority.jsonl`.
