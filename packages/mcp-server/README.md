# Neurobet Model Context Protocol (MCP) Server

Официальный MCP-сервер платформы Neurobet для интеграции с AI-агентами в Cursor, Antigravity, Claude Code и других средах разработки.

## Назначение

Позволяет AI-моделям и ассистентам:
1. **Инспектировать Observability**: опрашивать здоровье сервисов, метрики Prometheus, алерты и логи.
2. **Контролировать ML-модели**: просматривать реестр моделей, статус теневого режима (Shadow Mode), дрейф распределений (PSI и Wasserstein), тестировать инференс без риска, запускать обучение и проводить промоушен или откат.
3. **Аудировать ставки и риск**: анализировать виртуальный банкролл, 20 гейтов безопасности bet-manager, открытые и закрытые ставки.
4. **Следить за качеством данных**: проверять темпоральную целостность (защита от data leakage) и свежесть линии Fonbet.

## Запуск

Сервер работает поверх стандартного протокола JSON-RPC 2.0 (stdio) и не требует внешних pip-зависимостей (написан на стандартной библиотеке Python 3.10+).

```bash
# Автономный запуск
python3 /srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py

# Или через runner скрипт
bash /srv/neurobet/scripts/run_mcp_server.sh
```

## Конфигурация IDE

### Antigravity IDE (`.agents/mcp_config.json`)
```json
{
  "mcpServers": {
    "neurobet": {
      "command": "python3",
      "args": ["/srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py"],
      "env": {
        "NEUROBET_API_URL": "http://127.0.0.1:8005",
        "PROMETHEUS_URL": "http://127.0.0.1:9095"
      }
    }
  }
}
```

### Cursor IDE (`.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "neurobet": {
      "command": "python3",
      "args": ["/srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py"],
      "env": {
        "NEUROBET_API_URL": "http://127.0.0.1:8005",
        "PROMETHEUS_URL": "http://127.0.0.1:9095"
      }
    }
  }
}
```
