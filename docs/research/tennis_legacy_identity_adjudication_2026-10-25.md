# Ручная проверка legacy Flashscore identity links — 2026-10-25

Проверка проведена в production БД 2026-10-25T11:46:45.375775+00:00. Разобраны все 86 уникальных пар `(participant_id, source_player_id)`, для которых не было подтверждённой identity на `available_at` legacy facts. Проверялись сохранённые Flashscore player records и match observations: player ID/имя, URL, source content hash, parser version, сырые ID и имена сторон. Для каждой пары артефакт содержит покрытие исходными observations и примеры документов.

## Решения

- Подтверждено 15 одиночных player IDs по совпадению реестра игрока, participant alias, профиля и сохранённых match observations.
- Отклонено 70 составных doubles-side IDs: это ID парной стороны, а не отдельного игрока.
- Отклонена 1 связь Davidovich Fokina с дублирующим participant `156587f8-9544-40d8-8101-12fbabd4fcc3`; правильная identity уже подтверждена на отдельном участнике.
- Итого: 7,865 legacy fact versions, из них 7,353 относятся к подтверждённым парам и 512 к отклонённым. В последней версии было 85 из 86 пар.

## Сохранность и PIT

В `tennis_player_result_facts` не выполнялись INSERT/UPDATE/DELETE. Для каждой пары добавлено новое append-only решение в `tennis_player_identity_links` с текущим временем проверки, без backdate. Эти решения не подтверждают identity на старом `available_at`; старые факты сохраняют прежний исторический PIT статус. Исправление/инвалидация самих фактов не выполнялись.

Полная таблица 86 решений с evidence samples: [`tennis_legacy_identity_adjudication_2026-10-25.json`](tennis_legacy_identity_adjudication_2026-10-25.json).
