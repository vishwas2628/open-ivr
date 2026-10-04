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

* **one command** - `make install` installs Asterisk/ARI, then `make builder`
  opens a login-protected web builder and `make deploy` publishes it
* **no config files to hand-edit** - `config.yaml` (chmod 600) is the single
  source of truth, written by the installer, the builder and the CLI
* **real async** - [asyncari](https://github.com/M-o-a-T/asyncari) with
  `ToplevelChannelState` and the official `DTMFHandler`; barge-in works
* **server-side rendered** - FastAPI + Jinja, no frontend toolchain
* **standard library first** - logging handlers, CSV CDR, `smtplib`, `zoneinfo`

---

## Quick start

```sh
git clone <your fork> open-ivr && cd open-ivr
make venv                    # .venv + requirements.txt
make install                 # sudo: packages, Asterisk, ARI, systemd, permissions
make builder                 # http://127.0.0.1:8090
make deploy                  # render → copy into /etc/asterisk → reload
make verify                  # config, flow, prompts, ARI, ports
make service-start           # sudo: install/enable/start openivr.service
```

`make builder` prints a one-time password the first time it runs (the hash is
all that is kept). It binds loopback, so reach it through an SSH tunnel on a
remote machine:

```sh
ssh -L 8090:127.0.0.1:8090 user@your-server
```

`make deploy` never uses sudo. It renders the builder's JSON into
`data/asterisk-build/`, copies the files into `/etc/asterisk` (world-readable,
group-writable by `asterisk`), syncs `data/sounds/` into Asterisk's sounds
folder and reloads. If your user cannot write `/etc/asterisk`, run
`sudo make grant-permissions` once - after that deploy works unprivileged.

Lost the password? `make creds` resets the login and prints the new one once.

## Test call

```sh
asterisk -rx "channel originate PJSIP/6001 application Stasis openivr"
make logs
```

## Everyday commands

```sh
make help                    # every target
make status                  # Asterisk, openivr.service, config, prompts
make deploy                  # render + copy + sync prompts + reload
make staging                 # render only, into data/asterisk-build
make reload                  # reload Asterisk without restarting
make sounds                  # copy data/sounds into Asterisk only
make convert FILE=x.mp3      # convert one file to .ulaw + .wav
make config                  # where config.yaml lives, what is in it
make creds                   # reset the builder login
make verify                  # config, flow, media, ARI, ports
make openivr ARGS="flow tree"        # any CLI command
.venv/bin/python -m openivr run      # foreground runtime (debug friendly)
.venv/bin/python -m openivr originate PJSIP/6001   # test a target outside Stasis
make test                    # pytest
make lint                    # ruff
```

## How it works

```
caller ──SIP──▶ Asterisk ──Stasis(openivr)──▶ openivr core (asyncari)
                     ▲                              │
                     │  dial action: continueInDialplan│
                     └──────────[openivr-dial]──▶ Dial(PJSIP/…)

     menus / DTMF / prompts ──▶ sound:custom/…   (make deploy syncs data/sounds)
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
| `Makefile` | the entry points: `install`, `builder`, `deploy`, `verify`, `service-*` |
| `system/` | shell installer (`install.sh`, `steps/`) and `deploy.sh`/`status.sh` |
| `openivr/` | runtime core, config, publish pipeline, FastAPI builder |
| `data/` | `ivr_flow.json`, sounds, recordings, logs, staged configs (runtime state) |
| `systemd/openivr.service` | service unit (rendered with the current paths) |
| `docs/` | [index](docs/index.md), [getting started](docs/getting-started.md), [flow reference](docs/flow-reference.md), [configuration](docs/configuration.md), [operations](docs/operations.md), [troubleshooting](docs/troubleshooting.md), [development](docs/development.md) |
| `examples/` | the reference IVR this project was built to replace |

## Configuration

`config.yaml` is the single source of truth: hand-editable, `chmod 600`,
created from `example.config.yaml` on first run and written by the installer
(`openivr config set`), the builder (Settings, Permissions, SMTP) and
`make creds`. `data/permissions.json` keeps the recording/voicemail choices
next to the prompts they apply to. The most common knobs:

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

* ARI is bound to `127.0.0.1` and `config.yaml` is mode 600 - anyone with those
  credentials can control your calls, so never expose the ARI port
* the builder is login-protected (bcrypt + signed session cookie, default 2 h)
  and binds loopback; if you bind it publicly also set `OPENIVR_BUILDER_TOKEN`
* passwords are only ever stored hashed (builder) or in the 0600 `config.yaml`
  (ARI, SMTP) - use provider app passwords, not your main account password
* Python code never calls `sudo`; the installer does the privileged work once

## License

MIT - see [LICENSE](LICENSE).

Built on [asyncari](https://github.com/M-o-a-T/asyncari) and
[Asterisk](https://www.asterisk.org). Thanks to the Asterisk community - see
[docs/resources.md](docs/resources.md).