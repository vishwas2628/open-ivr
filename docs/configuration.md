# Configuration

Four layers, later wins:

1. dataclass defaults in `openivr/config.py`
2. `config.yaml` (checked in - your editable settings)
3. `system.json` (root - written by `sudo ./system/install.sh`, mode 600)
4. `data/smtp.json` (written by the builder)
5. `OPENIVR_*` environment variables

```sh
python -m openivr verify          # prints which layers were loaded
```

## `config.yaml`

### app

```yaml
app:
  name: openivr
  stasis_app: openivr     # must match Stasis(<name>) in the dialplan
  answer_delay: 0.4       # seconds of silence after Answer()
  max_call_seconds: 3600  # hard cap for record.ivr_leg
```

### ari

```yaml
ari:
  base_url: http://127.0.0.1:8088
  username: openivr
  password: ""            # normally comes from system.json
  reconnect_initial: 2    # exponential backoff, doubles up to reconnect_max
  reconnect_max: 60
```

### paths

```yaml
paths:
  flows: data/flows
  flow_file: data/ivr_flow.json
  sounds: data/sounds
  recordings: data/recordings
  logs: data/logs
  asterisk_spool: /var/spool/asterisk/recording   # where channel.record() writes
  asterisk_sounds: /var/lib/asterisk/sounds/custom # where sync-sounds.sh copies
```

Relative paths resolve against the project root (or `OPENIVR_ROOT`).

### logging

```yaml
logging:
  level: INFO
  file: data/logs/openivr.log
  max_bytes: 4194304    # 4 MiB per file
  backup_count: 5
  console: true
  color: true           # colours on a TTY, plain text in files
  format: "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s"
```

Logging goes through a `QueueHandler`/`QueueListener`, so a slow disk can never
stall the event loop. Every line carries time, level, logger, module and line
number.

### ivr

```yaml
ivr:
  prompt_timeout: 8       # fallback when a menu does not set one
  invalid_attempts: 2
  timeout_attempts: 2
  max_menu_depth: 3       # enforced by the flow validator
  max_options_per_menu: 5
  interdigit_timeout: 4   # gap allowed between collected digits
  goodbye_prompt: goodbye # only used if the flow has no "goodbye"
```

### dial

```yaml
dial:
  mode: dialplan        # dialplan = continueInDialplan + Dial() (clean CDR)
                        # originate = ARI originate + mixing bridge
  context: openivr-dial
  extension: s
  priority: 1
  originate_timeout: 30
  dial_timeout: 45
```

### voicemail

```yaml
voicemail:
  enabled: true
  dir: data/recordings/voicemail
  format: wav
  max_duration: 120
  silence_timeout: 4     # seconds of silence that ends the message
  beep: true
  terminate_on: any      # any DTMF ends the recording (Asterisk terminateOn)
  min_duration: 1        # shorter messages are discarded
  greeting_prompt: vm-greeting
  notify: true           # e-mail the message via SMTP
```

### cdr

```yaml
cdr:
  enabled: true
  backend: csv        # csv | postgres | both | none
  csv_file: data/logs/cdr.csv
  postgres: { host: 127.0.0.1, port: 5432, dbname: ivrdb, user: ivr, password: "" }
```

PostgreSQL needs `pip install psycopg[binary]`; the schema is created by
`system/steps/60-database.sh`.

### smtp / record / health

```yaml
smtp:  { enabled: false, host: "", port: 587, starttls: true, username: "",
         password: "", from_addr: "", alerts_to: [] }
record: { ivr_leg: false, dir: data/recordings/ivr }
health: { interval: 30 }
```

## `system.json`

Written by the installer, `chmod 600`:

```json
{
  "ari": { "base_url": "http://127.0.0.1:8088", "username": "openivr", "password": "…" },
  "extensions": { "count": 3, "start": 6001, "codec": "ulaw", "list": "6001,6002,6003" },
  "trunk": { "configured": true, "type": "sip", "name": "trunk1" },
  "db": { "enabled": true, "backend": "both", "dbname": "ivrdb" },
  "record": { "mixmonitor_dir": "/var/spool/asterisk/monitor", "keep_days": 30 },
  "smtp": { "enabled": true, "host": "smtp.gmail.com", "alerts_to": ["ops@example.com"] },
  "dialplan": { "context": "openivr-ivr", "dial_context": "openivr-dial" },
  "_meta": { "generated_at": "2026-01-01T10:00:00+00:00" }
}
```

Only the keys the IVR needs (`ari`, `extensions`, `db`, `smtp`, `record`,
`dialplan`) are read into the configuration; the rest is documentation.

## `data/smtp.json`

Written by the builder:

```json
{ "smtp": { "enabled": true, "host": "smtp.example.com", "port": 587,
            "starttls": true, "username": "ivr@example.com", "password": "…",
            "from_addr": "ivr@example.com", "alerts_to": ["ops@example.com"] } }
```

## Environment variables

Anything can be overridden, which is how the systemd unit is configured:

```sh
OPENIVR_ARI_BASE_URL=http://10.0.0.5:8088
OPENIVR_ARI_PASSWORD=secret
OPENIVR_LOG_LEVEL=DEBUG
OPENIVR_CDR_BACKEND=both
OPENIVR_CDR_POSTGRES_DBNAME=ivrdb     # OPENIVR_DB_* also works
OPENIVR_ROOT=/opt/openivr
```

Booleans accept `1/0/true/false/yes/no/on/off`; numbers are parsed; JSON
arrays/objects are parsed as JSON.