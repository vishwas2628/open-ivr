<pre align="center">
 ██████╗ ██████╗ ███████╗███╗   ██╗      ██╗██╗   ██╗██████╗ 
██╔═══██╗██╔══██╗██╔════╝████╗  ██║      ██║██║   ██║██╔══██╗
██║   ██║██████╔╝█████╗  ██╔██╗ ██║█████╗██║██║   ██║██████╔╝
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════╝██║╚██╗ ██╔╝██╔══██╗
╚██████╔╝██║     ███████╗██║ ╚████║      ██║ ╚████╔╝ ██║  ██║
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝      ╚═╝  ╚═══╝  ╚═╝  ╚═╝
</pre>
<div align="center">
  <b><i>self-hosted IVR for Asterisk</i></b><br>
  <i>shell installer &middot; web builder &middot; async ARI core</i>
</div>
<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/asterisk-20%2B-green.svg" alt="Asterisk 20+">
  <img src="https://img.shields.io/badge/asyncari-0.20%2B-orange.svg" alt="asyncari">
</p>

`openivr` turns a plain Asterisk box into an interactive voice menu system:

```text
"Press 1 for sales, 2 for support, 3 to leave a message..."
```

* **one command** - `./run_ivr.sh` installs Asterisk/ARI, opens a web builder,
  verifies everything and starts a systemd service
* **no config files to hand-edit** - one installer, one builder, one
  `system.json`
* **real async** - [asyncari](https://github.com/M-o-a-T/asyncari) with
  `ToplevelChannelState` and the official `DTMFHandler`; barge-in works
* **server-side rendered** - FastAPI + Jinja, no frontend toolchain
* **standard library first** - logging handlers, CSV CDR, `smtplib`, `zoneinfo`

---

## Quick start

```sh
git clone <your fork> open-ivr && cd open-ivr
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# optional: generate test prompts (espeak-ng/sox if available)
.venv/bin/python scripts/make_sounds.py --out data/sounds

./run_ivr.sh                 # 1. system module  2. builder  3. verify  4. service
```

`run_ivr.sh` step 2 serves the builder on <http://127.0.0.1:8090>; use an SSH
tunnel on remote machines:

```sh
ssh -L 8090:127.0.0.1:8090 user@your-server
```

When you hit *Finish*, the builder validates and saves `data/ivr_flow.json`,
stops itself, and the script continues: prompts are synced to Asterisk,
`python -m openivr verify` runs, and `open-ivr.service` starts.

## Test call

```sh
asterisk -rx "channel originate PJSIP/6001 application Stasis openivr s 1000"
journalctl -u open-ivr -f
```

## Everyday commands

```sh
.venv/bin/python -m openivr run              # foreground runtime (debug friendly)
.venv/bin/python -m openivr verify           # config, flow, media, ARI, ports
.venv/bin/python -m openivr builder          # edit the flow again
.venv/bin/python -m openivr flow tree        # menu tree as JSON
.venv/bin/python -m openivr flow validate    # validation only
.venv/bin/python -m openivr originate PJSIP/6001 1000   # test a target outside Stasis
.venv/bin/python -m openivr sounds list
sudo ./system/status.sh                      # Asterisk, ports, extensions, service
```

## How it works

```
caller ──SIP──▶ Asterisk ──Stasis(openivr)──▶ openivr core (asyncari)
                     ▲                              │
                     │  dial action: continueInDialplan│
                     └──────────[openivr-dial]──▶ Dial(PJSIP/…)

     menus / DTMF / prompts ──▶ sound:custom/…   (data/sounds → Asterisk)
     voicemail ──▶ channel.record() ──▶ data/recordings/voicemail/*.wav (+ SMTP)
     every call ──▶ data/logs/cdr.csv (+ PostgreSQL when enabled)
```

* **the dialplan stays in charge of `Dial()`** (`dial.mode: dialplan`) so CDR,
  `MixMonitor` and trunk billing behave exactly as without an IVR; set
  `dial.mode: originate` to bridge over ARI instead
* **business hours, time routes, greetings, retries, invalid-digit handling,
  digit collection and voicemail** are all data in the flow document
* **ARI reconnect** with exponential backoff; three consecutive failures raise
  an SMTP alert

## Project layout

| path | what it is |
|------|------------|
| `run_ivr.sh` | the orchestrator (plan steps 1–5) |
| `system/` | shell installer: preflight → Asterisk → firewall → ARI → trunk → extensions → database → recording → SMTP |
| `openivr/` | runtime core + the FastAPI builder |
| `data/` | `ivr_flow.json`, sounds, recordings, logs (runtime state) |
| `systemd/open-ivr.service` | service unit |
| `docs/` | [index](docs/index.md), [getting started](docs/getting-started.md), [flow reference](docs/flow-reference.md), [configuration](docs/configuration.md), [operations](docs/operations.md), [troubleshooting](docs/troubleshooting.md), [development](docs/development.md) |
| `examples/` | the reference IVR this project was built to replace |

## Configuration

`config.yaml` (hand-editable) is layered under `system.json` (written by the
installer, `chmod 600`), `data/smtp.json` (written by the builder) and
`OPENIVR_*` environment variables. The most common knobs:

```yaml
app:   { stasis_app: openivr, answer_delay: 0.4 }
ari:   { base_url: http://127.0.0.1:8088, username: openivr }
paths: { flow_file: data/ivr_flow.json, sounds: data/sounds }
ivr:   { prompt_timeout: 8, interdigit_timeout: 4, max_menu_depth: 3 }
dial:  { mode: dialplan, context: openivr-dial }
voicemail: { enabled: true, dir: data/recordings/voicemail, max_duration: 120 }
cdr:   { backend: csv, csv_file: data/logs/cdr.csv }
```

Full reference: [docs/configuration.md](docs/configuration.md).

## Documentation

* [Getting started](docs/getting-started.md)
* [Installing Asterisk](docs/installation.md) (packages **and** source build)
* [Asterisk setup for the IVR](docs/asterisk-setup.md) (ARI, dialplan, RTP, recording)
* [The IVR builder](docs/ivr-builder.md)
* [Flow reference](docs/flow-reference.md)
* [Configuration](docs/configuration.md)
* [Operations](docs/operations.md)
* [Troubleshooting](docs/troubleshooting.md)
* [Development](docs/development.md)
* [Resources](docs/resources.md)

## Requirements

* Linux with root (installer), systemd recommended
* Python 3.11+ in a virtualenv
* Asterisk 20+ (22.4 tested) with `res_ari`, `res_http_websocket`, `res_pjsip`
* optional: PostgreSQL 14+, `psycopg[binary]`, `sox`/`ffmpeg` for audio,
  `espeak-ng` for test prompts

## Security notes

* ARI is bound to `127.0.0.1` and `system.json` is mode 600 - anyone with those
  credentials can control your calls, so never expose the ARI port
* the builder binds loopback by default; if you bind it publicly, set
  `OPENIVR_BUILDER_TOKEN`
* e-mail passwords live in `data/smtp.json` (mode 600) - use provider app
  passwords, not your main account password

## License

MIT - see [LICENSE](LICENSE).

Built on [asyncari](https://github.com/M-o-a-T/asyncari) and
[Asterisk](https://www.asterisk.org). Thanks to the Asterisk community - see
[docs/resources.md](docs/resources.md).