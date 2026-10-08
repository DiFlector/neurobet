from typing import List, Dict, Any, Optional


class TennisSportAdapter:
    """
    Tennis Sport Adapter for Neurobet.
    Handles tennis-specific event normalization, hierarchical state tracking
    (match -> set -> game -> point), server indicators and market mapping.
    """
    sport_code: str = "tennis"

    def supported_markets(self) -> List[str]:
        return [
            "match_winner",
            "set_winner",
            "total_games",
            "handicap_games",
            "first_set_winner",
        ]

    def parse_event(self, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes tennis raw event data."""
        return {
            "sport_code": self.sport_code,
            "tournament": raw_payload.get("tournament", "ATP/WTA"),
            "participant_a": raw_payload.get("player_a", "Player A"),
            "participant_b": raw_payload.get("player_b", "Player B"),
            "surface": raw_payload.get("surface", "hard"),
        }

    def parse_state(self, raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parses hierarchical score and live stats for tennis."""
        return {
            "current_period": raw_payload.get("set", 1),
            "score": raw_payload.get("score", "0-0"),
            "server": raw_payload.get("server", "player_a"),
            "is_break_point": raw_payload.get("is_break_point", False),
            "stats": {
                "aces_a": raw_payload.get("aces_a", 0),
                "aces_b": raw_payload.get("aces_b", 0),
                "double_faults_a": raw_payload.get("df_a", 0),
                "double_faults_b": raw_payload.get("df_b", 0),
                "first_serve_win_pct_a": raw_payload.get("first_serve_win_pct_a", 0.0),
                "first_serve_win_pct_b": raw_payload.get("first_serve_win_pct_b", 0.0),
            },
        }

    def validate_sport_rules(self, proposal: Dict[str, Any]) -> bool:
        """Validate tennis bet proposal rules."""
        market = proposal.get("market")
        return market in self.supported_markets()
