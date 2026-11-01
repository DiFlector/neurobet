# PIT состояния Fonbet из сохранённого снимка БД

По указанию пользователя raw hash/object для состояния Fonbet больше не является обязательным PIT-гейтом. Политика `tennis_pit_db_state_v2` разрешает опираться на ID сохранённого Fonbet snapshot и время его наблюдения. Источник после cutoff, отсутствующий ID/evidence, некорректное состояние и отсутствующие признаки продолжают отклоняться. Для остальных пяти групп требования provenance сохранены. Это явное изменение политики доказательств, а не восстановление отсутствующих архивных объектов.

Изменены shared validator, builder и дополнительная проверка live readiness. В builder строки и summary включают policy version; audit и readiness используют тот же validator с версией в результате. Старые артефакты не изменялись.

## Проверка на сохранённой когорте

Прежний вход `tennis_training_examples_52bd46c9dfec.jsonl` обработан заново с тем же dataset cutoff `2026-10-27T09:00:00Z`: 7,792 примера, 0 принятых, 7,792 отклонённых. Причины `pit_evidence_hash_missing:event_state` и `pit_evidence_object_missing:event_state` отсутствуют; ранее каждая затрагивала 556 примеров. Остались format/surface 7,792; player prior 6,493; serve A/B 7,772/7,779; label availability 283. Для 50 примеров отсутствуют числовые поля состояния; исключение raw-гейта их не исправляет.

Это повторный builder по неизменённому audit input, без нового сканирования production БД. Пересчёт не означает свежий полный admission audit и не разрешает обучение.

## Проверки

Docker Research image: builder/shared validator/audit 58 passed, Neural forecast 26 passed. Покрыты отсутствие raw для DB snapshot, источник до/ровно на/после cutoff, отсутствие времени/ID, неразбираемое состояние; проверки других источников остались строгими. `git diff --check` и Compose validation прошли. Контрольные суммы входа и выходов сохранены в manifest рядом с отчётом. Обучение не запускалось.

## Развёртывание

Neural и Research пересобраны и перезапущены; оба healthy. В обоих работающих контейнерах импортирована `tennis_pit_db_state_v2`. Runtime self-check Neural подтвердил сертификацию своевременного DB snapshot без raw hash/object. Проверка выполнялась в памяти без записи тестового snapshot. Новые production forecasts используют эту политику; новый rolling/admission audit этим self-check не подменяется. Docker задерживал замену контейнеров; операция завершена, штатные имена сервисов восстановлены.
