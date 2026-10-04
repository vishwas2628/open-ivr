# Getting started

Ten minutes from a clean machine to a working IVR.

## 1. Requirements

* Ubuntu/Debian or RHEL-family Linux with root access
* Python 3.11+ (for the IVR itself)
* A SIP trunk for real calls, and/or a softphone for local testing
* ~2 GB RAM for Asterisk + PostgreSQL

## 2. Get the code and the Python environment

```sh
git clone <your fork> open-ivr && cd open-ivr
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Generate test prompts (optional but recommended)

```sh
# uses espeak-ng + sox when available, falls back to tones
.venv/bin/python scripts/make_sounds.py --out data/sounds
```

See [`data/sounds/README.md`](../data/sounds/README.md) for how to record or
convert your own prompts.

## 4. Run the whole pipeline

```sh
make venv
make install
make builder
```

Those targets perform plan steps 1–4:

1. **System module** – `sudo ./system/install.sh`:
   preflight → Asterisk → firewall → ARI → trunk → extensions + dialplan →
   database → recording → SMTP. Everything it learns is written to
   `system.json` in the project root.
2. **Builder** – opens `http://127.0.0.1:8090`:
   * *Media*: upload prompts
   * *Flow builder*: create menus and options
   * *SMTP*: mail settings for voicemail and alerts
   * *Finish*: validate → save → the builder shuts itself down
3. **Verification** – `python -m openivr verify` plus a prompt sync into
   `/var/lib/asterisk/sounds/custom`.
4. **Service** – installs and starts `open-ivr.service`.

Remote machines: tunnel the builder port instead of exposing it.

```sh
ssh -L 8090:127.0.0.1:8090 user@your-server
```

## 5. Place a test call

```sh
# local extension to extension, straight into the IVR
asterisk -rx "channel originate PJSIP/6001 application Stasis openivr s 1000"
```

* Hang up and call again between tries – ARI needs a fresh channel.
* Follow the log: `journalctl -u open-ivr -f`.
* Check the CDR: `column -s, -t data/logs/cdr.csv | tail`.

## 6. Everyday commands

```sh
.venv/bin/python -m openivr run          # run in the foreground (good for debugging)
.venv/bin/python -m openivr verify       # full check, exit code 0/1
.venv/bin/python -m openivr flow tree    # show the menu tree as JSON
.venv/bin/python -m openivr builder      # edit the flow again later
sudo ./system/status.sh                  # one-screen status of everything
```

## Where things are

| path | purpose |
|------|---------|
| `system.json` | what the installer collected (ARI creds, ports, extensions, DB, SMTP) |
| `config.yaml` | runtime configuration you can edit by hand |
| `data/ivr_flow.json` | the IVR flow (menus, options, actions) |
| `data/sounds/` | prompt audio (not in git) |
| `data/recordings/voicemail/` | stored voicemail, WAV |
| `data/logs/openivr.log` | rotating application log |
| `data/logs/cdr.csv` | call detail records |
| `data/smtp.json` | mail credentials (written by the builder) |