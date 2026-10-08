"""Sport-specific and generic Russian label provider."""

from typing import Dict
from .lifecycle import EventLifecycleState


class SportLabelProvider:
    """Provides human-friendly Russian titles for sports, markets, and outcomes."""

    SPORTS_RU: Dict[str, str] = {
        "tennis": "Теннис",
        "football": "Футбол",
        "hockey": "Хоккей",
        "basketball": "Баскетбол",
        "volleyball": "Волейбол",
        "table_tennis": "Настольный теннис",
        "esports": "Киберспорт",
    }

    MARKETS_RU: Dict[str, Dict[str, str]] = {
        "tennis": {
            "match_winner": "Победитель матча",
            "set_winner": "Победитель сета",
            "game_winner": "Победитель гейма",
            "total_games": "Тотал геймов",
            "total_sets": "Тотал сетов",
            "handicap_games": "Фора по геймам",
        },
        "football": {
            "match_winner": "Результат матча (1X2)",
            "double_chance": "Двойной исход",
            "total_goals": "Тотал голов",
            "both_teams_to_score": "Обе забьют",
        },
        "hockey": {
            "match_winner": "Победитель матча",
            "total_goals": "Тотал шайб",
        },
        "basketball": {
            "match_winner": "Победитель матча",
            "total_points": "Тотал очков",
        },
    }

    OUTCOMES_RU: Dict[str, Dict[str, str]] = {
        "tennis": {
            "player_a": "Игрок 1",
            "player_b": "Игрок 2",
            "over": "Больше",
            "under": "Меньше",
        },
        "football": {
            "team_a": "Команда 1 (П1)",
            "draw": "Ничья (X)",
            "team_b": "Команда 2 (П2)",
            "over": "Больше",
            "under": "Меньше",
        },
    }

    LIFECYCLE_RU: Dict[EventLifecycleState, str] = {
        EventLifecycleState.SCHEDULED: "Запланирован",
        EventLifecycleState.LIVE: "В прямом эфире (Live)",
        EventLifecycleState.PAUSED: "Перерыв",
        EventLifecycleState.SUSPENDED: "Приостановлен",
        EventLifecycleState.FINISHED: "Завершен",
        EventLifecycleState.CANCELLED: "Отменен",
    }

    @classmethod
    def get_sport_name_ru(cls, sport_code: str) -> str:
        """Returns Russian localized name for the sport, or capitalized code as fallback."""
        return cls.SPORTS_RU.get(sport_code.lower(), sport_code.capitalize())

    @classmethod
    def get_market_name_ru(cls, sport_code: str, market_type: str) -> str:
        """Returns Russian localized name for the betting market."""
        sport_markets = cls.MARKETS_RU.get(sport_code.lower(), {})
        if market_type in sport_markets:
            return sport_markets[market_type]
        # Generic fallback
        if market_type == "match_winner":
            return "Победитель события"
        return market_type.replace("_", " ").capitalize()

    @classmethod
    def get_outcome_name_ru(cls, sport_code: str, outcome_code: str) -> str:
        """Returns Russian localized name for the outcome selection."""
        sport_outcomes = cls.OUTCOMES_RU.get(sport_code.lower(), {})
        if outcome_code in sport_outcomes:
            return sport_outcomes[outcome_code]
        # Generic fallback
        generic_mapping = {
            "p1": "Участник 1",
            "p2": "Участник 2",
            "1": "Участник 1",
            "2": "Участник 2",
            "x": "Ничья",
            "player_a": "Участник 1",
            "player_b": "Участник 2",
            "team_a": "Команда 1",
            "team_b": "Команда 2",
            "over": "Больше",
            "under": "Меньше",
        }
        return generic_mapping.get(outcome_code.lower(), outcome_code)

    @classmethod
    def get_lifecycle_name_ru(cls, state: EventLifecycleState | str) -> str:
        """Returns Russian description of an event lifecycle state."""
        if isinstance(state, str):
            state = EventLifecycleState(state)
        return cls.LIFECYCLE_RU.get(state, str(state))
