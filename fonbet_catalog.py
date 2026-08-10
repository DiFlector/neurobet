"""
Fonbet Catalog Resolver
Handles loading factor descriptions, sport names, and outcome catalog metadata.
Prioritizes explicit CORE_FACTOR_MAP for exact outcome resolution (Тотал Больше, Тотал Меньше, П1, Х, П2).
"""

import json
import os
import httpx
from typing import Dict, Any, Optional

CORE_FACTOR_MAP: Dict[int, str] = {
    # Main outcomes
    921: "П1 (Победа хозяев/игрока 1)",
    922: "Х (Ничья)",
    923: "П2 (Победа гостей/игрока 2)",
    924: "1Х (Двойной шанс: 1 или Ничья)",
    925: "12 (Двойной шанс: 1 или 2)",
    926: "Х2 (Двойной шанс: Ничья или 2)",
    
    # Handicaps
    927: "Фора 1",
    928: "Фора 2",
    910: "Фора 1",
    912: "Фора 2",
    989: "Фора 1",
    991: "Фора 2",
    1569: "Фора 1",
    1572: "Фора 2",
    1677: "Фора 2",
    1678: "Фора 1",
    1680: "Фора 2",
    1681: "Фора 1",
    
    # Totals (Total Over = Больше, Total Under = Меньше)
    930: "Тотал Больше",
    931: "Тотал Меньше",
    1696: "Тотал Больше",
    1697: "Тотал Меньше",
    1727: "Тотал Больше",
    1728: "Тотал Меньше",
    1730: "Тотал Больше",
    1731: "Тотал Меньше",
    1733: "Тотал Больше",
    1734: "Тотал Меньше",
    1736: "Тотал Больше",
    1737: "Тотал Меньше",
    1739: "Тотал Больше",
    1740: "Тотал Меньше",
    1791: "Тотал Больше",
    1793: "Тотал Меньше",
    1794: "Тотал Больше",
    1796: "Тотал Меньше",
    1797: "Тотал Больше",
    1802: "Тотал Меньше",
    1804: "Тотал Больше",
    1805: "Тотал Меньше",

    # Individual Totals
    1809: "Индивидуальный тотал 1 Больше",
    1810: "Индивидуальный тотал 1 Меньше",
    1812: "Индивидуальный тотал 1 Больше",
    1813: "Индивидуальный тотал 1 Меньше",
    1815: "Индивидуальный тотал 1 Больше",
    1816: "Индивидуальный тотал 1 Меньше",
    1818: "Индивидуальный тотал 1 Больше",
    1819: "Индивидуальный тотал 1 Меньше",
    1821: "Индивидуальный тотал 1 Больше",
    1822: "Индивидуальный тотал 1 Меньше",
    
    1854: "Индивидуальный тотал 2 Больше",
    1855: "Индивидуальный тотал 2 Меньше",
    1857: "Индивидуальный тотал 2 Больше",
    1858: "Индивидуальный тотал 2 Меньше",
    1860: "Индивидуальный тотал 2 Больше",
    1861: "Индивидуальный тотал 2 Меньше",
    
    1871: "Обе забьют: Да",
    1873: "Обе забьют: Нет",
    1874: "Обе забьют: Да",
    1880: "Обе забьют: Нет",
    1881: "Обе забьют: Да",
    
    2820: "Проход в следующий раунд: Команда 1",
    2821: "Проход в следующий раунд: Команда 2",
    4241: "Спец-исход 1",
    4242: "Спец-исход 2"
}

class FonbetCatalog:
    def __init__(self, cache_file: str = "factor_catalog_cache.json"):
        self.cache_file = cache_file
        self.factors_map: Dict[int, str] = {}
        self.factors_group_map: Dict[int, str] = {}
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    self.factors_map = {int(k): v for k, v in cached_data.get("factors", {}).items()}
                    self.factors_group_map = {int(k): v for k, v in cached_data.get("groups", {}).items()}
            except Exception:
                pass

    def save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "factors": {str(k): v for k, v in self.factors_map.items()},
                    "groups": {str(k): v for k, v in self.factors_group_map.items()}
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def build_catalog_from_responses(self, items_or_data):
        """Processes API response items or list of responses to populate factor titles."""
        if not isinstance(items_or_data, list):
            items_or_data = [items_or_data]

        def process_node(node, current_path=""):
            if isinstance(node, dict):
                fid = node.get("factorId") or node.get("f") or node.get("id")
                fname = node.get("name") or node.get("title") or node.get("caption") or node.get("shortName")
                new_path = f"{current_path} | {fname}" if (fname and current_path) else (fname or current_path)
                
                if fid and isinstance(fid, (int, str)) and str(fid).isdigit():
                    int_fid = int(fid)
                    if fname and int_fid not in self.factors_map:
                        self.factors_map[int_fid] = str(fname)
                    if new_path and int_fid not in self.factors_group_map:
                        self.factors_group_map[int_fid] = str(new_path)

                for k, v in node.items():
                    process_node(v, new_path)
            elif isinstance(node, list):
                for item in node:
                    process_node(item, current_path)

        for data in items_or_data:
            process_node(data)
        
        self.save_cache()

    def resolve_factor_label(self, factor_id: int, param_str: Optional[str] = None) -> str:
        """
        Returns a human-readable title for a factor ID, formatted with any line parameter.
        Prioritizes CORE_FACTOR_MAP to guarantee explicit outcomes (Тотал Больше / Тотал Меньше).
        """
        title = CORE_FACTOR_MAP.get(factor_id) or self.factors_group_map.get(factor_id) or self.factors_map.get(factor_id) or f"Фактор {factor_id}"
        
        if param_str is not None and str(param_str).strip() != "":
            p_clean = str(param_str).strip()
            if "(" in title and ")" in title:
                return title
            return f"{title} ({p_clean})"
        return title
