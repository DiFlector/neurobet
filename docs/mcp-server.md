# Neurobet Model Context Protocol (MCP) Server — Руководство и Спецификация

## 1. Назначение и Архитектура

**Neurobet MCP Server** — полнофункциональный сервер по стандарту **Model Context Protocol (MCP 2024-11-05)**, предназначенный для прямого подключения AI-ассистентов в средах разработки (**Antigravity IDE**, **Cursor**, **Claude Code**, **Claude Desktop**).

Сервер позволяет моделям:
- **Наблюдать за состоянием кластера**: собирать метрики Prometheus, статус всех 13 контейнеров, здоровье сервисов и алерты.
- **Управлять ML-моделями**: отслеживать активные и теневые (Shadow Mode) модели, вычислять дрейф распределений вероятностей (PSI, Wasserstein distance), проводить контрфактический анализ ставок, запускать обучение, выполнять промоушен или экстренный откат (rollback).
- **Контролировать виртуальный банкролл и риск**: инспектировать баланс, 20 защитных гейтов `bet-manager`, виртуальные ставки и историю расчетов.
- **Тестировать инференс без риска**: эмулировать предсказания на произвольных векторах признаков в sandbox-режиме без влияния на реальный банкролл.

### Архитектурные особенности:
- **Нулевые внешние pip-зависимости ядра**: сервер написан исключительно на стандартной библиотеке Python 3.10+ (`urllib`, `json`, `sys`), что гарантирует моментальный запуск в любой среде без конфликтов виртуальных окружений.
- **Двунаправленный stdio JSON-RPC 2.0 интерфейс**: полностью соответствует стандарту MCP specification.
- **Отказоустойчивость**: встроенные таймауты и структурная обработка ошибок HTTP/Prometheus.

---

## 2. Подключение к IDE

### 2.1 Antigravity IDE
Файл конфигурации расположен в [`.agents/mcp_config.json`](file:///srv/neurobet/.agents/mcp_config.json):
```json
{
  "mcpServers": {
    "neurobet": {
      "command": "python3",
      "args": [
        "/srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py"
      ],
      "env": {
        "NEUROBET_API_URL": "http://127.0.0.1:8005",
        "PROMETHEUS_URL": "http://127.0.0.1:9095",
        "PYTHONPATH": "/srv/neurobet/packages/mcp-server/src"
      }
    }
  }
}
```

### 2.2 Cursor IDE
Файл конфигурации расположен в [`.cursor/mcp.json`](file:///srv/neurobet/.cursor/mcp.json):
```json
{
  "mcpServers": {
    "neurobet": {
      "command": "python3",
      "args": [
        "/srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py"
      ],
      "env": {
        "NEUROBET_API_URL": "http://127.0.0.1:8005",
        "PROMETHEUS_URL": "http://127.0.0.1:9095",
        "PYTHONPATH": "/srv/neurobet/packages/mcp-server/src"
      }
    }
  }
}
```

### 2.3 Ручной запуск / Тестирование
```bash
# Через исполняемый скрипт
bash scripts/run_mcp_server.sh

# Запуск верификационного тест-сьюта
python3 tests/mcp/test_mcp_server.py
```

---

## 3. Полный каталог инструментов (Tools)

| Название инструмента | Назначение | Ключевые параметры |
|---|---|---|
| `neurobet_get_system_health` | Сводка здоровья всех сервисов, БД, Redis, моделей и очередей | `{}` |
| `neurobet_query_prometheus` | Выполнение PromQL-запросов к живому Prometheus | `query: string` |
| `neurobet_get_prometheus_targets` | Статус таргетов сбора метрик Prometheus | `{}` |
| `neurobet_list_models` | Список всех моделей в реестре (ACTIVE, SHADOW, RETIRED) | `{}` |
| `neurobet_get_active_model` | Рабочая модель для спорта (теннис, футбол и др.) | `sport_code?: string` |
| `neurobet_get_shadow_report` | Полный отчет теневого режима: дрейф PSI, Wasserstein, PnL | `sport_code?: string, min_days?: number` |
| `neurobet_register_shadow_model` | Постановка модели-кандидата в Shadow Mode | `candidate_version_tag: string, sport_code?: string` |
| `neurobet_promote_model` | Промоушен теневой модели в ACTIVE с проверкой гейтов | `shadow_version_tag: string, force?: boolean` |
| `neurobet_rollback_model` | Экстренный откат активной модели к предыдущей версии | `sport_code?: string, target_version_tag?: string, reason?: string` |
| `neurobet_simulate_inference` | Безопасный sandbox-тест инференса на векторе признаков | `features: object, odds?: object, sport_code?: string` |
| `neurobet_trigger_training` | Запуск фонового обучения новой модели | `sport_code?: string, model_type?: string, hyperparameters?: object` |
| `neurobet_list_training_runs` | История тренировок, метрики валидации | `sport_code?: string` |
| `neurobet_list_experiments` | Список проведенных бэктестов и экспериментов | `{}` |
| `neurobet_compare_experiments` | Сравнение двух экспериментов (ROI, Drawdown, p-value) | `baseline_id: string, candidate_id: string` |
| `neurobet_list_live_events` | Список текущих событий из Fonbet collector | `sport_code?: string, status?: string, limit?: number` |
| `neurobet_get_event_timeline` | Таймлайн счета и игровых состояний матча | `event_id: string` |
| `neurobet_get_event_odds` | Коэффициенты и динамика изменения линии | `event_id: string` |
| `neurobet_get_predictions` | ML-предсказания, вероятности и value/edge | `event_id?: string, sport_code?: string` |
| `neurobet_get_research_packet` | Материалы Web Research, новости, травмы | `event_id?: string` |
| `neurobet_get_bankroll_summary` | Баланс виртуального банкролла, экспозиция, P&L | `{}` |
| `neurobet_list_virtual_bets` | Журнал симулированных ставок | `status?: string, limit?: number` |
| `neurobet_list_bet_proposals` | Кандидаты ставок и результаты 20 гейтов риска | `limit?: number` |
| `neurobet_sync_settlements` | Запуск расчета завершенных матчей | `{}` |
| `neurobet_get_data_quality_report` | Аудит монотонности времени и отсутствия leakage | `{}` |
| `neurobet_get_scheduler_stats` | Очередь и статистика диспетчеризации кандидатов | `{}` |
| `neurobet_trigger_backtest` | Исторический replay ACTIVE/SHADOW модели или всего спорта. Боевой баланс не меняется | `scope`, `sport_code`, `model_version_id`, гейты и банкролл |
| `neurobet_get_backtest_run` | Статус и полный отчёт replay, включая ставки | `run_id` |
| `neurobet_list_backtest_runs` | Последние replay без списка ставок | `sport_code`, `limit` |

---

## 4. Динамические ресурсы (Resources)

- `neurobet://system/health`: оперативный JSON-снимок состояния системы.
- `neurobet://models/registry`: каталог всех моделей и их текущих статусов.
- `neurobet://bankroll/status`: оперативное состояние виртуального банкролла.
- `neurobet://metrics/prometheus`: ключевой дамп метрик Section 41.

---

## 5. Встроенные промпты (Prompts)

- `analyze_model_drift`: регламентный анализ дрейфа PSI и рекомендация по промоушену / переобучению.
- `audit_bet_decision`: пошаговое расследование конкретной ставки (коэффициенты, фичи, решение bet-manager).
- `evaluate_experiment_results`: экспертная оценка результатов нового эксперимента относительно бейзлайна.

---

## 6. Метрики и дашборды Grafana

В дополнение к базовому дашборду `neurobet-overview.json` в `infra/grafana/dashboards/` развернуты:
1. **`neurobet-models-drift.json`**:
   - Объем предсказаний активной и теневой моделей
   - Дрейф распределений PSI и Wasserstein distance
   - Скорость изменения Edge (Value Drift Velocity)
   - Распределение вердиктов LLM (AGREE / DISAGREE / PASS)
   - Задержка инференса LLM (p95) и кэш Web Research
2. **`neurobet-bankroll-risk.json`**:
   - Динамика доступного банкролла и заблокированной экспозиции (RUB)
   - Общий капитал портфеля (Total Portfolio Equity)
   - Воронка предложений: Proposals vs Rejections vs Executed
   - Доля отклонений 20 защитными гейтами Bet-Manager
   - Пропускная способность расчетов (Settlements / sec)
