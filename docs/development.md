# Development

## Layout

```
openivr/
├── openivr/                the importable package (runtime)
│   ├── config.py           dataclass config + layered loader
│   ├── flow.py             flow schema, parsing, validation
│   ├── state.py            the IVR state machine (ToplevelChannelState + DTMFHandler)
│   ├── runner.py           ARI connection, event dispatch, health, reconnect
│   ├── voicemail.py        channel.record() voicemail
│   ├── originate.py        dialplan-mode and originate-mode dialing
│   ├── cdr.py              CSV / PostgreSQL call detail records
│   ├── notify.py           SMTP voicemail mail + ARI alerts
│   ├── dtmf.py             one-shot and collecting DTMF helpers
│   ├── media.py            prompt resolution, upload checks
│   ├── context.py          container wiring
│   ├── logging_setup.py    QueueHandler + RotatingFileHandler
│   ├── builder/            the FastAPI builder (server-side rendered)
│   └── __main__.py         CLI: run, verify, builder, originate, flow, sounds
├── system/                 shell installer (steps 00…80) + status/sync helpers
├── scripts/make_sounds.py  test-prompt generator
├── systemd/open-ivr.service
├── tests/                  pytest suite (offline)
├── examples/               the reference IVR this project replaces
└── data/                   flow, sounds, recordings, logs (runtime state)
```

## The event model (the part that matters)

asyncari owns one websocket. Each channel becomes an `IVRState`, a
`ToplevelChannelState` subclass, registered by unique channel id.

* `on_start(StasisStart)` starts the call task with a `start_soon` and returns
  `None` immediately - never `await` the whole call inside `on_start`, or no
  further events are dispatched and DTMF stops working.
* `on_DtmfReceived`, `on_PlaybackFinished`, `on_ChannelDestroyed`,
  `on_StasisEnd`, `on_ChannelHangupRequest` are override points. The base
  class calls `super()` for anything it needs to keep its own bookkeeping
  (`context` attribute, hangup events, DTMF).
* The call task waits on two primitives:

  * `DigitStream` - a memory object stream. The DTMF handler pushes, the
    waiter collects. Digits during playback *cancel* the playback (barge-in).
  * `PlaybackHandle` - a `PlaybackFinished` waiter with a polling fallback for
    old ARI versions.

* `on_StasisEnd` and `on_ChannelDestroyed` cancel the call task, so a caller
  hanging up mid-prompt ends the task immediately.

Because `on_start` returns early, the loop never blocks; because everything
else is cancellation-based, there is no race between "prompt finished" and
"caller hung up".

## Shared flow module

`flow.py` is the single source of truth:

* `Flow.load()` / `Flow.from_dict()` parse `data/ivr_flow.json`
* `flow.validate(with_media=True)` checks structure *and* prompt files
* the builder posts forms → `flow_from_form()` → the same `Flow` object →
  serialised back to disk

The runtime only ever executes a `Flow` instance that passed `validate()`,
which is why the builder cannot save a flow that the runtime would reject.

## Local development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"      # or: pip install -r requirements.txt
.venv/bin/python -m compileall -q openivr
.venv/bin/ruff check openivr tests
.venv/bin/pytest -q
```

Test without a live Asterisk:

```sh
.venv/bin/python -m openivr verify     # config/flow/media checks; ARI check fails harmlessly
.venv/bin/python -m openivr flow tree
NONINTERACTIVE=1 ./run_ivr.sh --skip-install --no-builder --no-start
```

## Tests

`tests/` uses pytest and never touches the network or the filesystem outside
`tmp_path`:

* `test_config.py` – layer precedence, env overrides, list parsing
* `test_flow.py` – parsing, validation errors (unreachable menu, depth, options)
* `test_dtmf.py` – collector/one-shot behaviour with `anyio` memory streams
* `test_voicemail.py` – filename sanitisation, spool → project move, too-short
  messages, greeting playback
* `test_state.py` – menu dispatch with a **fake channel**, digits, barge-in,
  timeout/invalid retry counters, hangup
* `test_cdr.py` – CSV escaping and event ordering
* `test_builder.py` – form → flow round trip, media validation, SMTP round trip
  via `httpx` + `fastapi.testclient`

Add new behaviour to the test suite before the builder UI: the flow validator
is what keeps builder and runtime in sync.

## Adding a new action

1. add the action to `flow.py` (validation) and to
   `builder/defaults.py` + `templates/builder.html` (UI)
2. implement it in `_run_action()` in `state.py`
3. add CDR events where it makes sense
4. add tests in `tests/test_state.py` and mention it in `docs/flow-reference.md`

## Code style

* Python 3.11+, four-space indent, no inline comments - docstrings instead.
* Async code uses `anyio` (`anyio.sleep`, task groups, memory streams); `asyncio`
  only where the web server requires it.
* Prefer standard-library implementations (logging handlers, `csv`,
  `smtplib`, `zoneinfo`, `urllib.parse`).
* Official asyncari APIs only. If a private attribute seems necessary, check
  `https://github.com/M-o-a-T/asyncari` first - the plan explicitly forbids
  reimplementing what asyncari already does.