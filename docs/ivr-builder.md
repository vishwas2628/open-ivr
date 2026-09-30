# The IVR builder

`./run_ivr.sh` (step 2) starts the builder on `http://127.0.0.1:8090`. It is a
plain FastAPI app with server-side rendered forms - no build step, no
JavaScript framework. You can start it again at any time:

```sh
.venv/bin/python -m openivr builder --port 8090          # add --keep-open to not self-exit
```

## Pages

| page | what it is for |
|------|----------------|
| `/` | dashboard: validation status, prompt count, mail status, flow tree |
| `/build` | the flow builder: menus, options, actions, timeouts |
| `/media` | upload / delete prompt audio |
| `/smtp` | mail server, credentials and alert recipients |
| `/finish` | final validation, then save + shutdown |
| `/status` | JSON status (used for scripting) |

## Workflow

1. **Media first.** Upload `.wav` prompts. Only 8/16 kHz mono 16-bit files are
   accepted; the upload form tells you the exact `sox` command when a file has
   the wrong format. Every uploaded name becomes a selectable prompt in the
   builder, and a prompt referenced by the flow but missing on disk is reported
   as a *problem* on `/finish` (and logged as a warning at runtime).
2. **Flow builder.** Every menu is a card in one form:
   * menu prompt, timeout seconds/retries/prompt, invalid-digit retries/prompt
   * after retries are exhausted: hang up, another menu, or a dial endpoint
   * one row per key (`0-9`, `*`, `#`); tick *on* to enable, then pick the
     action and fill in its fields
   * limits are enforced by the shared validator: at most
     `ivr.max_options_per_menu` keys and `ivr.max_menu_depth` levels
3. **SMTP.** Voicemail mail and the "ARI unreachable" alarm both go through
   it. The *Test connection* button logs in to your server immediately.
4. **Finish.** Validates structure *and* media, saves `data/ivr_flow.json`, and
   - because the plan asks for it - redirects to `/smtp` first when voicemail is
   enabled and no mail settings exist yet. After that the builder shuts itself
   down and `run_ivr.sh` continues with verification and the systemd unit.

## What the builder writes

| file | when |
|------|------|
| `data/ivr_flow.json` | every save |
| `data/sounds/*` | uploads |
| `data/smtp.json` | SMTP form |

The builder never touches `system.json` or `config.yaml`, so the machine
configuration collected by the installer stays untouched.

## Safety

* It binds `127.0.0.1`; POSTs from non-loopback clients are rejected.
* To use it from another host, either tunnel it or set a token:

```sh
OPENIVR_BUILDER_TOKEN=$(openssl rand -hex 16) \
  .venv/bin/python -m openivr builder --host 0.0.0.0
# every POST then needs ?token=... (or the X-Openivr-Token header)
```

## Tips

* `*` is a good "go back" key, `#` a good "confirm/finish" key for `collect`.
* Keep prompts short; barge-in makes long prompts pointless.
* Leave `timeout.max_retries` at 1 and point `fail_action` at the main menu
  rather than hanging up - callers appreciate a second chance.