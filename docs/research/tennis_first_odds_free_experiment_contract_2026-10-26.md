# Контракт первого odds-free эксперимента

Решение от 2026-10-26: использовать неизменённый ordered feature allowlist `tennis_odds_free_v1` из `services/neural/app/tennis_forecast.py`. Это решение фиксирует обработку неизвестных входов; admission к обучению остаётся закрытым.

- **Surface:** использовать только подтверждённое значение с собственным `surface_available_at <= cutoff`; иначе выставлять `surface_unknown=1` и остальные surface flags в 0. Не выводить покрытие из турнира, пола, тура или текущего metadata.
- **Serve:** использовать только валидный point-opportunity numerator/denominator, связанный с подтверждённой личностью и доступный к cutoff. Если его нет, использовать `serve_probability=0.62`, `serve_sample_weight=0`. Текущие, поздние или retrospective строки игнорировать.
- **Missingness:** priors/unknown являются входным представлением отсутствия данных, а не наблюдаемым покрытием; ими нельзя выполнять admission thresholds.
- **Format:** `best_of` и `best_of_available_at <= cutoff` обязательны. При отсутствии PIT-доказательства пример не сертифицируется, match-win недоступен, обучение запрещено.
- **Compatibility:** существующие признаки и сохранённые прогнозы не менять; любое будущее изменение состава/семантики требует новой версии контракта.

Проверенное основание: feature-source audit от 2026-10-25 содержит 0/4,826 полностью сертифицированных примеров, 0/4,826 observed serve с знаменателем на каждой стороне и 0/4,826 известных surface. См. [`tennis_feature_source_completeness_2026-10-25.md`](tennis_feature_source_completeness_2026-10-25.md). Решение соответствует текущим builder defaults и не добавляет синтетические source timestamps.

Фактический dataset builder и PIT coverage ещё требуют отдельной сертификации; это не отчёт о допуске. Никакие fit, holdout evaluation, registry или SHADOW действия не выполнялись и этим решением не разрешаются.
