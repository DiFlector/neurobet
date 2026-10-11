"""
HTTP Client for Neurobet MCP Server.
Implements resilient communication with Neurobet Backend API and Prometheus using urllib.
Zero external dependencies.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class NeurobetClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        prometheus_url: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or os.getenv("NEUROBET_API_URL", "https://necrolich.ru/diflector/neurobet")).rstrip("/")
        self.prometheus_url = (prometheus_url or os.getenv("PROMETHEUS_URL", "")).rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Executes HTTP request and parses JSON response."""
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        headers = {
            "User-Agent": "Neurobet-MCP-Agent/1.0",
            "Accept": "application/json",
        }
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            import ssl
            ssl_ctx = ssl.create_default_context()
            try:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            except Exception:
                pass
            with urllib.request.urlopen(req, timeout=self.timeout, context=ssl_ctx) as resp:
                resp_text = resp.read().decode("utf-8")
                if not resp_text:
                    return {}
                try:
                    return json.loads(resp_text)
                except json.JSONDecodeError:
                    return {"raw_text": resp_text}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                return {"error": f"HTTP {e.code}", "detail": err_json}
            except Exception:
                return {"error": f"HTTP {e.code}", "detail": err_body}
        except urllib.error.URLError as e:
            return {"error": "Connection failed", "detail": str(e.reason)}
        except Exception as e:
            return {"error": "Request failed", "detail": str(e)}

    # Backend API methods
    def get_health(self) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/health")

    def get_system_status(self) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/system/status")


    def get_sports(self) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/sports")

    def get_events(self, sport_code: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/events", params={"sport_code": sport_code, "status": status, "limit": limit})

    def get_event_detail(self, event_id: str) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/events/{event_id}")

    def get_event_timeline(self, event_id: str) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/events/{event_id}/timeline")

    def get_event_odds(self, event_id: str) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/events/{event_id}/odds")

    def get_predictions(self, event_id: Optional[str] = None, sport_code: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if event_id:
            return self._request(f"{self.base_url}/api/events/{event_id}/predictions")
        return self._request(f"{self.base_url}/api/predictions", params={"sport_code": sport_code, "limit": limit})

    def get_research(self, event_id: Optional[str] = None) -> Any:
        if event_id:
            return self._request(f"{self.base_url}/api/events/{event_id}/research")
        return self._request(f"{self.base_url}/api/research")

    # Models & Shadow Mode
    def list_models(self) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/models")

    def get_shadow_models(self, sport_code: str = "tennis") -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/models/shadow", params={"sport_code": sport_code})

    def get_shadow_report(self, sport_code: str = "tennis", min_days: float = 7.0) -> Dict[str, Any]:
        return self._request(
            f"{self.base_url}/api/models/shadow/report",
            params={"sport_code": sport_code, "min_shadow_period_days": min_days},
        )

    def register_shadow_model(self, candidate_tag: str, sport_code: str = "tennis") -> Dict[str, Any]:
        return self._request(
            f"{self.base_url}/api/models/shadow/register",
            method="POST",
            data={"sport_code": sport_code, "candidate_version_tag": candidate_tag},
        )

    def promote_shadow_model(self, shadow_tag: str, sport_code: str = "tennis", force: bool = False) -> Dict[str, Any]:
        return self._request(
            f"{self.base_url}/api/models/shadow/promote",
            method="POST",
            data={"sport_code": sport_code, "shadow_version_tag": shadow_tag, "force": force},
        )

    def rollback_model(self, sport_code: str = "tennis", target_tag: Optional[str] = None, reason: str = "Agent rollback") -> Dict[str, Any]:
        return self._request(
            f"{self.base_url}/api/models/rollback",
            method="POST",
            data={"sport_code": sport_code, "target_version_tag": target_tag, "reason": reason},
        )

    def simulate_inference(self, features: Dict[str, float], odds: Optional[Dict[str, float]] = None, sport_code: str = "tennis") -> Dict[str, Any]:
        return self._request(
            f"{self.base_url}/api/models/simulate-inference",
            method="POST",
            data={"sport_code": sport_code, "features": features, "odds": odds},
        )

    def trigger_training(self, sport_code: str = "tennis", model_type: str = "gbdt", hyperparameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request(
            f"{self.base_url}/api/models/train",
            method="POST",
            data={"sport_code": sport_code, "model_type": model_type, "hyperparameters": hyperparameters or {}},
        )

    def list_training_runs(self, sport_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/training/runs", params={"sport_code": sport_code})

    def list_experiments(self) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/experiments")

    def compare_experiments(self, exp_a: str, exp_b: str) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/experiments/comparison", params={"baseline_id": exp_a, "candidate_id": exp_b})

    # Bankroll & Betting
    def get_bankroll(self) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/bankroll")

    def list_bets(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/bets", params={"status": status, "limit": limit})

    def list_proposals(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._request(f"{self.base_url}/api/proposals", params={"limit": limit})

    def sync_settlements(self) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/settlement/sync", method="POST")

    def get_data_quality_report(self) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/quality/report")

    def get_scheduler_stats(self) -> Dict[str, Any]:
        return self._request(f"{self.base_url}/api/scheduler/stats")

    # Prometheus API
    def query_prometheus(self, query: str) -> Dict[str, Any]:
        if self.prometheus_url:
            return self._request(f"{self.prometheus_url}/api/v1/query", params={"query": query})
        return self._request(f"{self.base_url}/api/prometheus/query", params={"query": query})

    def get_prometheus_targets(self) -> Dict[str, Any]:
        if self.prometheus_url:
            return self._request(f"{self.prometheus_url}/api/v1/targets")
        return self._request(f"{self.base_url}/api/prometheus/targets")

