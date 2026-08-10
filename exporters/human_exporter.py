"""
Human-Readable Exporter for Fonbet Live Line Data.
Generates clean JSON, formatted TXT report, CSV spreadsheet, and an interactive HTML report.
"""

import json
import os
import csv
from typing import List, Dict, Any

class HumanExporter:
    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def export(self, parsed_events: List[Dict[str, Any]], timestamp_str: str) -> Dict[str, str]:
        """Export all human-friendly format files."""
        files_created = {}

        # 1. Export JSON (Structured for human inspection)
        json_path = os.path.join(self.output_dir, "live_odds_human.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "meta": {
                    "source": "Fonbet Live",
                    "url": "https://fon.bet/live",
                    "timestamp": timestamp_str,
                    "total_events": len(parsed_events)
                },
                "events": parsed_events
            }, f, ensure_ascii=False, indent=2)
        files_created["json"] = json_path

        # 2. Export TXT (Readable text report)
        txt_path = os.path.join(self.output_dir, "live_odds_human.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"=====================================================\n")
            f.write(f"         FONBET LIVE ODDS REPORT ({timestamp_str})\n")
            f.write(f"         Всего событий в лайве: {len(parsed_events)}\n")
            f.write(f"=====================================================\n\n")

            current_sport = None
            for ev in parsed_events:
                sport = ev.get("sport_path", "Другие виды спорта")
                if sport != current_sport:
                    current_sport = sport
                    f.write(f"\n─────────────────────────────────────────────────────\n")
                    f.write(f" 🏆 {sport.upper()}\n")
                    f.write(f"─────────────────────────────────────────────────────\n")

                match_name = ev.get("match_name")
                score = f" [{ev['score']}]" if ev.get("score") else ""
                status = f" ({ev['timer']})" if ev.get("timer") else ""
                f.write(f"\n📍 {match_name}{score}{status}\n")
                f.write(f"   ID события: {ev['event_id']} | Рычагов/коэффициентов: {len(ev['odds'])}\n")
                f.write(f"   -------------------------------------------------\n")

                for odd in ev.get("odds", []):
                    f.write(f"   • {odd['label']:<40} : {odd['coefficient']}\n")
        files_created["txt"] = txt_path

        # 3. Export CSV (Flat table spreadsheet)
        csv_path = os.path.join(self.output_dir, "live_odds_human.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp", "Event ID", "Sport", "Match", "Score", "Timer",
                "Factor ID", "Outcome Label", "Parameter", "Coefficient"
            ])
            for ev in parsed_events:
                for odd in ev.get("odds", []):
                    writer.writerow([
                        timestamp_str,
                        ev.get("event_id"),
                        ev.get("sport_path"),
                        ev.get("match_name"),
                        ev.get("score", ""),
                        ev.get("timer", ""),
                        odd.get("factor_id"),
                        odd.get("label"),
                        odd.get("parameter", ""),
                        odd.get("coefficient")
                    ])
        files_created["csv"] = csv_path

        # 4. Export Interactive HTML Report
        html_path = os.path.join(self.output_dir, "live_odds_human.html")
        html_content = self._generate_html_report(parsed_events, timestamp_str)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        files_created["html"] = html_path

        return files_created

    def _generate_html_report(self, events: List[Dict[str, Any]], timestamp_str: str) -> str:
        events_json = json.dumps(events, ensure_ascii=False)
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fonbet Live Odds Monitor</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-green: #22c55e;
            --coeff-bg: #0f172a;
            --coeff-hover: #3b82f6;
        }}
        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 20px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            color: var(--accent);
        }}
        .meta-badge {{
            background: #1e293b;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 13px;
            color: var(--text-muted);
            border: 1px solid var(--border-color);
        }}
        .search-bar {{
            width: 100%;
            padding: 12px 18px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            color: var(--text-main);
            font-size: 15px;
            margin-bottom: 20px;
            box-sizing: border-box;
        }}
        .event-card {{
            background: var(--card-bg);
            border-radius: 10px;
            border: 1px solid var(--border-color);
            padding: 18px;
            margin-bottom: 16px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }}
        .sport-tag {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--accent);
            font-weight: 600;
            margin-bottom: 6px;
        }}
        .match-title {{
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
        }}
        .score {{
            color: var(--accent-green);
            font-size: 16px;
        }}
        .odds-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 10px;
            margin-top: 14px;
        }}
        .odd-box {{
            background: var(--coeff-bg);
            padding: 10px 14px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: border-color 0.2s;
        }}
        .odd-box:hover {{
            border-color: var(--accent);
        }}
        .odd-label {{
            font-size: 13px;
            color: var(--text-muted);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 140px;
        }}
        .odd-val {{
            font-weight: 700;
            font-size: 15px;
            color: #facc15;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>⚽ Fonbet Live Odds Explorer</h1>
        <div class="meta-badge">Обновлено: {timestamp_str} | Событий: {len(events)}</div>
    </div>
    <input type="text" id="searchInput" class="search-bar" placeholder="Поиск по матчу, виду спорта или исходу...">
    
    <div id="eventsContainer"></div>

    <script>
        const eventsData = {events_json};
        const container = document.getElementById('eventsContainer');
        const searchInput = document.getElementById('searchInput');

        function renderEvents(filterText = '') {{
            container.innerHTML = '';
            const lowerFilter = filterText.toLowerCase();

            eventsData.forEach(ev => {{
                const matchText = ev.match_name.toLowerCase();
                const sportText = ev.sport_path.toLowerCase();
                const hasMatchingOdds = ev.odds.some(o => o.label.toLowerCase().includes(lowerFilter));

                if (!filterText || matchText.includes(lowerFilter) || sportText.includes(lowerFilter) || hasMatchingOdds) {{
                    const card = document.createElement('div');
                    card.className = 'event-card';

                    let oddsHtml = ev.odds.map(o => `
                        <div class="odd-box">
                            <span class="odd-label" title="${{o.label}}">${{o.label}}</span>
                            <span class="odd-val">${{o.coefficient}}</span>
                        </div>
                    `).join('');

                    card.innerHTML = `
                        <div class="sport-tag">${{ev.sport_path}}</div>
                        <div class="match-title">
                            <span>${{ev.match_name}}</span>
                            <span class="score">${{ev.score || ''}} ${{ev.timer ? '(' + ev.timer + ')' : ''}}</span>
                        </div>
                        <div class="odds-grid">${{oddsHtml}}</div>
                    `;
                    container.appendChild(card);
                }}
            }});
        }}

        searchInput.addEventListener('input', (e) => renderEvents(e.target.value));
        renderEvents();
    </script>
</body>
</html>
"""
