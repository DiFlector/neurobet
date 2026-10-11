# Neurobet Disaster Recovery & Restore Runbook

> **Phase 31**: Disaster Recovery, Business Continuity, and Audit Verification.

---

## 1. Objectives & Metrics (RPO / RTO)

| Metric | Target | Description |
|---|---|---|
| **RPO (Recovery Point Objective)** | **< 1 hour** | Максимально допустимая потеря данных при сбое. Снимки линий и событий собираются непрерывно, дамп БД делается периодически. |
| **RTO (Recovery Time Objective)** | **< 15 минут** | Время от объявления аварии до восстановления полной работоспособности стека и завершения реконсилиации баланса. |
| **Ledger Integrity** | **100% (Zero discrepancy)** | Ни одна копейка виртуального баланса не может быть потеряна или искажена. Баланс восстанавливается из `ledger_entries`. |
| **Decision Auditability** | **100% Provenance** | Любая зарегистрированная ставка восстанавливается до первоисточника: Event + Features + ML Prediction + LLM Verdict + Gate Checks. |

---

## 2. Архитектура резервного копирования

Резервные копии формируются утилитой `scripts/backup_manager.py` в директорию `/srv/neurobet/backups/`:

```text
backups/
├── dr_manifest_<timestamp>.json       # Манифест с SHA256-хэшами и метаданными
├── postgres/
│   ├── neurobet_pg_<db>_<ts>.dump     # Бинарный сжатый pg_dump (Custom format -Fc)
│   └── neurobet_pg_<db>_<ts>.meta.json
├── minio/
│   ├── minio_data_<ts>.tar.gz         # Архив файлового хранилища MinIO (/data)
│   └── minio_data_<ts>.meta.json
├── models/
│   ├── models_export_<ts>.tar.gz      # Чекпоинты .joblib, датасеты, веса
│   └── models_export_<ts>.meta.json
└── configs/
    ├── config_export_<ts>.tar.gz      # .env, compose, prometheus, migrations
    └── config_export_<ts>.meta.json
```

### Команда создания полного бэкапа:
```bash
python3 /srv/neurobet/scripts/backup_manager.py --all
```

---

## 3. Процедура восстановления (Step-by-Step Restore)

### Шаг 1. Восстановление конфигурации
Если сервер или директория разворачиваются заново:
```bash
tar -xzf /path/to/config_export_<ts>.tar.gz -C /srv/neurobet/
chmod 600 /srv/neurobet/.env
```

### Шаг 2. Запуск инфраструктурных сервисов
Перед восстановлением БД запускаются только изолированные контейнеры хранения данных:
```bash
cd /srv/neurobet
docker compose up -d postgres minio redis
# Дождаться статуса healthy
docker compose ps
```

### Шаг 3. Восстановление базы данных PostgreSQL (TimescaleDB)
Для восстановления из дампа используется `scripts/restore_manager.py`:

```bash
# Восстановление в тестовую БД для проверки целостности:
python3 /srv/neurobet/scripts/restore_manager.py \
  --postgres backups/postgres/neurobet_pg_neurobet_<ts>.dump \
  --target-db neurobet_restore_test

# Или прямое восстановление в рабочую БД neurobet:
docker exec -i neurobet_postgres pg_restore -U neurobet -d neurobet --clean --if-exists < backups/postgres/neurobet_pg_neurobet_<ts>.dump
```

Проверка целостности гипертаблиц TimescaleDB:
```bash
docker exec -it neurobet_postgres psql -U neurobet -d neurobet -c "
SELECT hypertable_name, num_chunks, compression_enabled 
FROM timescaledb_information.hypertables;
"
```

### Шаг 4. Восстановление MinIO (Raw Snapshots & HTML)
```bash
docker exec -i neurobet_minio tar -xzf - -C /data < backups/minio/minio_data_<ts>.tar.gz
```

### Шаг 5. Восстановление артефактов моделей (Model Checkpoints)
```bash
python3 /srv/neurobet/scripts/restore_manager.py \
  --models backups/models/models_export_<ts>.tar.gz
```
Проверка контрольных сумм:
```bash
sha256sum -c <(jq -r '.artifacts[] | "\(.sha256)  models/checkpoints/\(.name)"' backups/models/models_export_<ts>.meta.json)
```

### Шаг 6. Холодный запуск платформы (Full Stack Cold Start)
После восстановления хранилищ запускается весь стек:
```bash
cd /srv/neurobet
docker compose up -d
```

Проверка готовности всех микросервисов:
```bash
# Проверка эндпоинтов здоровья
curl -f http://127.0.0.1:8005/health
curl -f http://127.0.0.1:9005/
docker exec neurobet_redis redis-cli -a "$REDIS_PASSWORD" ping
```

---

## 4. Реконсилиация виртуального банкролла (Ledger Balance Audit)

Правило архитектуры: **Баланс не может изменяться произвольно — он строго равен начальному депозиту плюс сумме записей журнала `ledger_entries`**.

В случае сбоя или подозрения на повреждение данных запускается аудит:
```bash
docker exec -i neurobet_backend python - << 'EOF'
from db.connection import SessionLocal
from db.models.betting import VirtualAccount
from bankroll.reconciliation import ReconciliationEngine

with SessionLocal() as session:
    accounts = session.query(VirtualAccount).all()
    for acc in accounts:
        report = ReconciliationEngine.reconcile_account(session, acc.id)
        status = "PASSED" if report.is_balance_reconciled else "FAILED"
        print(f"Account: {acc.name} [{status}] | Balance: {report.actual_balance} RUB | Recalculated: {report.recalculated_balance} RUB | Diff: {report.balance_difference}")
        if not report.is_balance_reconciled:
            print(f"  Discrepancies: {len(report.discrepancies)}")
EOF
```

---

## 5. Аудит и реконструкция решения по ставке (Decision Provenance)

Для любой ставки в системе `bets.id` система обязана восстановить ответ на вопрос:
> *«Почему Neurobet сделала эту ставку в этот конкретный момент?»*

Скрипт проверки цепочки принятия решения:
```bash
docker exec -i neurobet_backend python - << 'EOF'
import sys
from db.connection import SessionLocal
from db.models.betting import Bet, BetProposal, BetValidationResult, LedgerEntry, BetSettlement
from db.models.predictions import MLPrediction, LLMDecision
from db.models.events import Event

with SessionLocal() as session:
    bet = session.query(Bet).order_by(Bet.placed_at.desc()).first()
    if not bet:
        print("No bets found.")
        sys.exit(0)

    proposal = session.get(BetProposal, bet.proposal_id)
    validation = session.query(BetValidationResult).filter_by(proposal_id=proposal.id).first()
    event = session.get(Event, bet.event_id)
    ml_pred = session.get(MLPrediction, proposal.ml_prediction_id) if proposal.ml_prediction_id else None
    llm_dec = session.get(LLMDecision, proposal.llm_decision_id) if proposal.llm_decision_id else None
    ledger_entry = session.query(LedgerEntry).filter_by(bet_id=bet.id).first()
    settlement = session.query(BetSettlement).filter_by(bet_id=bet.id).first()

    print("================ BET AUDIT TRAIL ================")
    print(f"Bet ID:              {bet.id}")
    print(f"Placed At:           {bet.placed_at} UTC")
    print(f"Event:               {event.participant_a_name} vs {event.participant_b_name} ({event.sport_code})")
    print(f"Market & Selection:  {bet.market} -> {bet.outcome} @ {bet.odds}")
    print(f"Stake & Potential:   {bet.stake} RUB -> payout {bet.potential_payout} RUB")
    print(f"ML Model:            {ml_pred.model_version if ml_pred else 'N/A'} (Prob: {proposal.model_probability}, Edge: {proposal.edge})")
    print(f"LLM Verdict:         {llm_dec.verdict if llm_dec else 'N/A'} (Reason: {llm_dec.reasoning[:60] if llm_dec else 'N/A'})")
    print(f"Manager Validation:  Accepted={validation.is_accepted if validation else 'N/A'}, Checks={validation.checks_passed if validation else []}")
    print(f"Financial Ledger:    Type={ledger_entry.entry_type if ledger_entry else 'N/A'}, Amount={ledger_entry.amount if ledger_entry else 'N/A'}")
    print(f"Settlement:          Status={settlement.status if settlement else 'PENDING'}, Profit={settlement.net_profit if settlement else 'N/A'}")
    print("=================================================")
EOF
```

---

## 6. Регламент аварийных сценариев (Disaster Scenarios)

1. **Отказ диска/хоста**:
   - Развернуть новый хост из базового образа.
   - Скачать последний бандл `backups/dr_manifest_<ts>.json`.
   - Выполнить `config_export` -> `postgres` -> `minio` -> `models` -> `docker compose up -d`.
   - Запустить `ReconciliationEngine` для верификации балансов.
2. **Сбой/падение контейнера Postgres**:
   - `docker compose restart postgres`.
   - При повреждении WAL: остановить контейнер, восстановить из последнего `.dump` файла.
3. **Обнаружение расхождения баланса**:
   - Перевести `bet-manager` в аварийный режим `SAFE_MODE` (блокировка новых ставок).
   - Запустить скрипт реконсилиации для выявления точной транзакции `ledger_entries`, вызвавшей расхождение.
   - Сформировать запись типа `ADJUSTMENT` в `ledger_entries` с указанием причины и подписью оператора.
