# Flashscore: локальное восстановление и отключение общего cooldown

Текущее состояние 25 октября 2026 года: общий cooldown снят и отключён по прямому запросу пользователя. Локальное восстановление включено для обычной очереди. Research, Flashscore worker и FlareSolverr healthy.

## Настройки и очередь

- `FLASHSCORE_RECOVERY_ENABLED=true`.
- `FLASHSCORE_SOURCE_COOLDOWN_ENABLED=false`: глобальную паузу больше не устанавливают ни обычный throttle handler, ни failure handler FlareSolverr. Dispatcher/network regulator/recovery lease игнорируют старое значение при выключенном флаге. В БД подтверждено `cooldown_until=NULL` после снятия паузы и повторных запросов.
- `FLASHSCORE_CAPTCHA_RETRY_SECONDS=60`: неудачная CAPTCHA job сохраняет checkpoint и повторяется автоматически. CAPTCHA больше не паркует архив/профиль в `blocked`. Обычный 429 сохраняет backoff конкретной задачи, без остановки всего источника.
- Возвращены в очередь 11 archive imports и 30 player jobs. Attempts и checkpoints сохранены; старый `captcha_retry_at` убран из progress. Работающие jobs не переоткрывались.
- CAPTCHA и обычный HTTP 403 запускают локальный FlareSolverr; HTTP остаётся первым способом при отсутствии действующей solver-сессии. Существующий интервал начала запросов и лимит браузеров сохранены.

## Что реализовано

FlareSolverr закреплён по digest, не публикует порт и запускается обычным Compose как зависимость Research/worker. Sidecar резервирует один из существующих двух browser slots. Платных solver API и ручного UI нет. Сессия пригодна 15 минут; PostgreSQL lease с heartbeat/fencing сериализует обращения, не удерживая SQL-транзакцию во время сетевого I/O. Неактивная проверка наличия сессии не затирает последний реальный результат восстановления.

Upstream игнорирует request headers и подставляет HTTP 200. Локальный bootstrap выставляет штатные `x-fsign`/Referer только для точного feed host/path, очищает их после операции и считывает фактический HTTP-статус из Chrome PerformanceNavigationTiming. Неизвестный HTTP-статус не считается успехом; API envelope `status="ok"` сохраняется отдельно. На локальном HTTP 404 endpoint подтверждён `solution.status=404`, тестовая сессия удалена.

Диагностика и архивы содержат `fetch_transport`, `challenge_type`, `recovery_result`. Cookies/токены в application logs и БД не сохраняются. Общий Web Research fetcher не изменялся.

## Реальные проверки

[Основная acceptance-проверка](flashscore_recovery_acceptance_2026-10-25.json) завершилась сразу после снятия паузы:

- Получены и архивированы 13 матчей профиля Huang Tsung-Hao.
- Получен и сохранён PBP документ `juGuZdcD`: 190 подтверждённых очков, без gaps.
- Эти ответы пришли обычным HTTP без CAPTCHA. Поэтому `verified=false`: это подтверждение доступности данных, а не решённой капчи.

[Дополнительная проверка транспорта](flashscore_recovery_browser_probe_2026-10-25.json): два реальных запроса профиля через локальный FlareSolverr вернули HTTP 200 и примерно 1.45 MB HTML каждый. Последующая PBP-проверка снова вернула 190 подтверждённых очков через `http_feed`; она не объявляется проверкой browser-feed обхода. Явная CAPTCHA в контрольных ответах не наблюдалась; `captcha_verified=false`.

Включение восстановления и отключение cooldown выполнены независимо от этого ограничения по прямому указанию пользователя. Универсальное автоматическое прохождение любой CAPTCHA не заявляется.

## Верификация выпуска

**181 passed, 1 deselected**: профильные тесты охватывают recovery, importer/worker, browser pool, PBP и общий Research, включая реальные PostgreSQL-проверки в изолированных временных схемах. Один старый `test_end_to_end_research_pipeline` исключён после отдельного воспроизведения прежнего сбоя в старом контейнере: новость с фиксированной датой 9 октября теперь получает `freshness_score=0`. Код и тест свежести не менялись.

Миграция `055_flashscore_recovery` применена. Compose config и `git diff --check` проходят. Обучение моделей, пороги и Fonbet links не менялись.
