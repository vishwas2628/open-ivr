# Operations

## Service

```sh
sudo systemctl status open-ivr
sudo systemctl restart open-ivr
journalctl -u open-ivr -f
sudo systemctl edit open-ivr      # e.g. Environment=OPENIVR_LOG_LEVEL=DEBUG
```

The unit runs as `asterisk` from `/opt/openivr` with `PYTHONUNBUFFERED=1`,
restarts on failure (`Restart=always`, `RestartSec=5`) and stops on `SIGTERM`
(handled by the runner, which cancels every in-flight call task).

The unit paths follow wherever you cloned: `sudo make service-start` renders
`systemd/openivr.service` with the current paths and enables it. To run
without systemd:

```sh
.venv/bin/python -m openivr run
```

## Logs

* console: coloured, `time | LEVEL | logger | module:line | message`
* `data/logs/openivr.log`: rotating, 4 MiB × 5 files
* `journalctl -u open-ivr` under systemd

Useful log lines:

```text
ARI connected (http://127.0.0.1:8088)
[a1b2c3d4e5f60718] call from 1001 on 1699…       StasisStart
[a1b2c3d4e5f60718] menu main digit 2 -> submenu support
[a1b2c3d4e5f60718] voicemail for 1001 saved: …/openivr-support-….wav (11s)
[a1b2c3d4e5f60718] finished outcome=hangup cause=normal duration=42.3s digits=2,0
health: asterisk Asterisk 22.4.0 up 3h12m | ivr calls 1 | cdr csv
```

Raise verbosity with `logging.level: DEBUG` or `OPENIVR_LOG_LEVEL=DEBUG`; DTMF
and playback lines then appear.

## CDR

`data/logs/cdr.csv`, one row per event:

| column | meaning |
|--------|---------|
| `call_id` | 16 hex chars, also in the log lines |
| `seq` | event order within the call |
| `event` | `stasis_start`, `answered`, `menu`, `dtmf`, `selection`, `invalid`, `timeout`, `voicemail`, `collected`, `dial`, `dial_result`, `played`, `barge_in`, `hangup`, `stasis_end` |
| `menu`, `digit` | context of the event |
| `duration` | seconds since StasisStart |
| `cause` | `normal`, `busy`, `congestion`, … |

With `cdr.backend: both` the same call is inserted once into the `cdr` table
(`call_id`, `caller`, `duration`, `digits`, `variables`, `events`).

Quick look:

```sh
column -s, -t data/logs/cdr.csv | tail -20
sqlite3 /dev/null "select 1"   # (just to have a shell handy)
psql -d ivrdb -c "select caller, outcome, duration from cdr order by started_at desc limit 10"
```

## Recordings

* Voicemail: `data/recordings/voicemail/openivr-<mailbox>-<ts>-<id>.wav`,
  e-mailed when SMTP is configured.
* `record.ivr_leg: true` records the whole IVR leg through ARI, paused while
  prompts play, then continues. When the call ends the file is moved out of the
  Asterisk spool into `record.dir` (`data/recordings/ivr`).
* Human-to-human legs (dialplan `Dial()`) are recorded with `MixMonitor` if you
  answered yes in step 50 → `/var/spool/asterisk/monitor`.

`/etc/cron.d/openivr-recordings` prunes files older than `keep_days`.

## Monitoring / alerts

* `health.interval` (default 30 s) logs an Asterisk uptime line.
* If the ARI websocket cannot be established three times in a row, the IVR
  sends `[openivr] ARI connection lost` to `smtp.alerts_to`, and
  `[openivr] ARI connection restored` when it is back.
* `systemctl is-active open-ivr` and
  `grep -c 'finished outcome' data/logs/openivr.log` are enough for a
  Nagios/Icinga check; Prometheus users can scrape the health line or use the
  `verify` command.

## Reconnecting

The runner reconnects forever with exponential backoff
(`ari.reconnect_initial` → `ari.reconnect_max`). Asterisk restarts
(`asteriskctl restart`) are therefore harmless: calls in flight are lost, new
calls work again within seconds.

## Rotating the flow

Keep versioned copies of good flows:

```sh
cp data/ivr_flow.json data/flows/$(date +%F-%H%M).json
python -m openivr flow validate --file data/flows/2026-01-01-1200.json
```

## Backup

Worth backing up: `config.yaml`, `system.json` (mode 600), `data/ivr_flow.json`,
`data/sounds/`, `data/recordings/`. Losing `data/logs/cdr.csv` is acceptable
if the PostgreSQL backend is enabled.