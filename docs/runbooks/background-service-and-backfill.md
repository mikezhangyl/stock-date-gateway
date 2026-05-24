# Background Service and CYQ Backfill

This runbook keeps the local gateway available for upstream projects and runs a
daily CYQ cache warmup without importing code from other projects.

## Gateway Service

The development service script loads `.env.local`, then starts Uvicorn:

```bash
scripts/dev_gateway.sh
```

Install the LaunchAgent example:

```bash
mkdir -p ~/Library/LaunchAgents
cp launchd/com.stock-data-gateway.dev.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.stock-data-gateway.dev.plist
```

Check it:

```bash
curl http://127.0.0.1:8700/api/health
tail -f /tmp/stock-data-gateway.err.log
```

Stop it:

```bash
launchctl unload ~/Library/LaunchAgents/com.stock-data-gateway.dev.plist
```

## CYQ Backfill

Run once:

```bash
CYQ_TS_CODES=600519.SH,000001.SZ scripts/cyq_backfill_daily.sh
```

Optional environment:

```bash
CYQ_START_DATE=20260518
CYQ_END_DATE=20260525
MARKET_DATA_HOME=/Users/mikezhang/Coding/AI-Learning/stock-data-gateway/data
```

Install the daily LaunchAgent example:

```bash
mkdir -p ~/Library/LaunchAgents
cp launchd/com.stock-data-gateway.cyq-backfill.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.stock-data-gateway.cyq-backfill.plist
```

Before using it for a larger universe, edit `CYQ_TS_CODES` in the plist or set a
wrapper script that exports the desired symbols. Keep secrets in `.env.local`;
the plist should not contain tokens.

## Cache Checks

```bash
uv run market-gateway-cache inspect --provider tushare --endpoint cyq_chips
uv run market-gateway-cache audit --provider tushare --endpoint cyq_chips
```
