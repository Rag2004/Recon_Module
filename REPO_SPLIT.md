# Recon Module is its own GitHub repo

Canonical remote: https://github.com/Rag2004/Recon_Module

This tree compares the strategy CSV, Mongo positions, and the broker position
book, then writes `recon_alerts`. It does not place orders.

## Layout

- Own `Dockerfile` (build from this repo root only)
- Zero source coupling to QuantEngine, RMS, or TOE
- Desk orchestration is docker compose via `RECON_MODULE_DIR`

## Runtime contract

| Contract | Value |
|----------|--------|
| Health | `GET /health` on port 8013 |
| Alerts | Mongo `recon_alerts` |
| Strategy book | `{POSITION_STATE_DIR}/open_positions.csv` |
| Infra book | Mongo `positions` filtered by `broker_id` |
| Broker modes | `kotak` (`broker:kotak:prod` then Neo `positions()`), `dhan`, `mock` |
| Desk build | `RECON_MODULE_DIR=/opt/Recon_Module` |

On the broker desk, `RECON_BROKER_MODE=kotak` and the infra filter is `kotak_sandbox`.
