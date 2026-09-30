# Flow reference

The flow is one JSON document, `data/ivr_flow.json`. `openivr/flow.py` owns the
schema and the validator - the builder and the runtime share it, so anything
the builder accepts is executable.

## Skeleton

```json
{
  "version": 1,
  "start_menu": "main",
  "welcome": "welcome",
  "goodbye": "goodbye",
  "time_route": null,
  "menus": {
    "main": {
      "prompt": "main-menu",
      "timeout": {
        "seconds": 8,
        "prompt": "timeout-msg",
        "max_retries": 1,
        "fail_action": { "action": "hangup" }
      },
      "invalid": { "prompt": "invalid-msg", "max_retries": 1 },
      "options": {
        "1": { "action": "dial", "label": "Sales", "endpoint": "PJSIP/1001" },
        "2": { "action": "submenu", "label": "Support", "target": "support" },
        "3": {
          "action": "voicemail",
          "label": "Leave a message",
          "mailbox": "support",
          "greeting": "vm-support",
          "after": { "action": "hangup" }
        },
        "4": {
          "action": "collect",
          "prompt": "enter-account",
          "min_digits": 4,
          "max_digits": 8,
          "variable": "account",
          "next": { "action": "dial", "endpoint": "PJSIP/1002" }
        },
        "9": { "action": "repeat" },
        "0": { "action": "dial", "label": "Operator", "endpoint": "PJSIP/1000" }
      }
    }
  }
}
```

## Menu settings

| key | meaning |
|-----|---------|
| `prompt` | prompt played when the menu opens (barge-in enabled) |
| `timeout.seconds` | how long to wait for a digit *after* the prompt |
| `timeout.prompt` / `timeout.max_retries` | played on silence, N times |
| `timeout.fail_action` | what happens when the retries run out (`hangup`, `submenu`, `dial`) |
| `invalid.prompt` / `invalid.max_retries` | played for an unknown digit, N times, then `fail_action` |
| `on_exit` | optional action for leaving this menu (used as fallback by the two retry counters) |
| `options` | digit → action; at most `ivr.max_options_per_menu` entries |

Attempt counters reset as soon as the caller picks a valid key, and they are
tracked per menu, so a caller cannot get stuck by alternating invalid digits and
timeouts.

## Actions

| action | fields | behaviour |
|--------|--------|-----------|
| `dial` | `endpoint`, `label` | passes the channel to `[openivr-dial]` and lets the dialplan `Dial()` it (`dial.mode: dialplan`, the default). With `dial.mode: originate` it originates the endpoint and bridges it instead. |
| `submenu` / `goto` | `target` | enter another menu |
| `voicemail` | `mailbox`, `greeting` (required), `prompt`, `after` | plays the greeting, records with `channel.record()`, moves the file to `data/recordings/voicemail/`, mails it, then runs `after` (or repeats the menu) |
| `collect` | `prompt`, `min_digits`, `max_digits`, `variable`, `next` | collects digits with an interdigit timeout (`#` confirms), stores them under `variable` in the CDR, then runs `next` |
| `repeat` | – | replays the menu prompt |
| `hangup` | – | plays `goodbye` and releases the channel |
| `time_route` | `business_menu`, `after_hours_menu`, `hours` | per-menu business-hours routing |

Collected values are visible in `data/logs/cdr.csv` (`detail=account=1234`) and
in the PostgreSQL `cdr.variables` column.

## Business hours

Top-level optional routing (see `openivr.state.in_business_hours`):

```json
"time_route": {
  "prompt": "office-closed",
  "timezone": "Asia/Kolkata",
  "business_menu": "sales",
  "after_hours_menu": "voicemail",
  "hours": {
    "mon": ["09:00-18:00"], "tue": ["09:00-18:00"], "wed": ["09:00-18:00"],
    "thu": ["09:00-18:00"], "fri": ["09:00-18:00"],
    "sat": ["10:00-14:00"], "sun": []
  }
}
```

Days missing from `hours`, or listed as `[]`, are closed. Times accept
`"09:00-18:00"` or `["09:00", "18:00"]`. If `zoneinfo` has no data for the
timezone, the code logs a warning and falls back to UTC.

## Prompts

Prompt names are resolved as `sound:custom/<name>`, because `run_ivr.sh` copies
`data/sounds/` into `/var/lib/asterisk/sounds/custom/`. You may also write a
full media URI in a flow (`sound:hello-world`, `recording:stored:foo`) and it is
passed to Asterisk unchanged.

## Validation rules

`python -m openivr flow validate` (and the builder) enforce:

* `start_menu` exists, every `target`/`business_menu`/`after_hours_menu` exists
* no menu is unreachable from `start_menu`
* ≤ `ivr.max_options_per_menu` options per menu, ≤ `ivr.max_menu_depth` deep
* every key is one of `0-9 * #`
* `dial` needs an `endpoint`, `voicemail` needs a `greeting`,
  `collect` needs `min_digits ≤ max_digits`
* with media enabled: every referenced prompt has a file in `data/sounds/`

## Direct entry (optional)

The dialplan can pass a dialled extension to Stasis:

```asterisk
Stasis(openivr,s,${CALLERID(num)},${DIALED_EXTEN})
```

If that argument is digits, the IVR uses its first digit as the answer to the
first menu (skipping `welcome`) - handy for "call your extension, get straight
into that department".