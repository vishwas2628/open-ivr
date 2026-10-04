# openivr documentation

Self-hosted IVR for Asterisk: a shell installer, a web builder, and an async
ARI core written with [asyncari](https://github.com/M-o-a-T/asyncari).

## Start here

| guide | what it covers |
|-------|----------------|
| [getting-started.md](getting-started.md) | end-to-end: install → build → first call |
| [installation.md](installation.md) | Asterisk itself (packages and source build) |
| [asterisk-setup.md](asterisk-setup.md) | ARI, dialplan, extensions, trunks, RTP |
| [ivr-builder.md](ivr-builder.md) | the web builder: menus, media, SMTP |
| [flow-reference.md](flow-reference.md) | the flow document: every menu setting and action |
| [configuration.md](configuration.md) | `config.yaml`, `system.json`, `data/smtp.json`, env vars |
| [operations.md](operations.md) | systemd, logs, CDR, recordings, monitoring |
| [troubleshooting.md](troubleshooting.md) | the problems you will actually hit |
| [development.md](development.md) | architecture, code layout, tests |
| [resources.md](resources.md) | Asterisk / ARI learning links |

## How the pieces fit together

```
caller ──SIP──▶ Asterisk ──Stasis(openivr)──▶ openivr core (asyncari)
                     ▲                              │
                     │                              ├─ menus / DTMF / prompts
                     │                              ├─ voicemail (channel.record)
                     │                              ├─ CDR (CSV / PostgreSQL)
                     │                              └─ alerts + voicemail mail (SMTP)
                     │
   dial action ─────┴──▶ [openivr-dial] ──▶ Dial(PJSIP/1001)
```

The Makefile drives the whole pipeline:

1. **system module** – `sudo ./system/install.sh` writes everything it learns
   into `system.json` (one JSON file, as the plan asks).
2. **builder** – a FastAPI app at `http://127.0.0.1:8090` where you upload
   prompts, draw the menus, validate, and finally stop the builder.
3. **verification** – `python -m openivr verify` checks config, flow, media,
   ARI reachability and ports; prompts are synced into Asterisk's sounds folder.
4. **service** – installs and starts the `open-ivr.service` systemd unit.

## Design promises

* **Official asyncari objects** – the core subclasses `ToplevelChannelState`,
  uses the `DTMFHandler` mix-in and the standard `channel.play`,
  `channel.record`, `channel.continueInDialplan`, `client.channels.originate`
  operations. No private attributes, no hand-rolled websocket handling.
* **Short `on_start`** – events keep flowing while a call is in progress;
  `StasisEnd` cancels the call task immediately.
* **Barge-in everywhere** – any digit stops the current prompt.
* **Standard library first** – `logging.handlers.QueueListener` for logging,
  `csv` for the CDR, `smtplib` for mail, `zoneinfo` for business hours.
  Third-party runtime deps are only `asyncari`, `anyio`, `PyYAML`, `fastapi`,
  `uvicorn`, `jinja2`, `python-multipart`.
* **Everything is data** – the flow lives in `data/ivr_flow.json`; the builder
  and the runtime share one validator (`openivr/flow.py`), so what the builder
  accepts is exactly what the runtime can execute.