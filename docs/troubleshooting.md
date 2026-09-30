# Troubleshooting

## The IVR never connects

```text
ARI connection failed (1): ConnectError …
```

```sh
curl -u openivr:PASSWORD http://127.0.0.1:8088/ari/asterisk/info
asteriskctl status
asterisk -rx "http show status"
grep -A3 '^\[user\]' /etc/asterisk/ari.conf      # user/password
```

* `enabled = no` in `http.conf` → the HTTP listener is off.
* Credentials mismatch between `/etc/asterisk/ari.conf` and `system.json`
  (`ari.username`, `ari.password`). Re-run
  `sudo ./system/install.sh --steps 30-ari.sh`.
* Firewall or `bindaddr` too narrow.
* `python -m openivr verify` prints the exact failure.

## No audio / prompts are silent

1. Does the file exist and is it the right format?
   `ls data/sounds` and `soxi data/sounds/main-menu.wav` (8 kHz, mono, 16 bit).
2. Is it synced into Asterisk?
   `ls /var/lib/asterisk/sounds/custom`
3. Can Asterisk play it directly?
   `asterisk -rx "channel originate Local/6001@test application Playback custom/main-menu"`
4. Log line `no audio file for prompt 'x'` means the flow references a prompt
   that is missing - `python -m openivr flow validate` lists them all.

## Calls enter Stasis but nothing happens

```sh
asterisk -rx "ari show channels"      # is the channel visible?
tail -f data/logs/openivr.log
```

* The `Stasis()` app name must equal `app.stasis_app` (`openivr`). If
  `python -m openivr run` does not log `call from …`, the dialplan entered a
  *different* app - check `asterisk -rx "dialplan show context openivr-ivr"`.
* The dialplan must `Answer()` before Stasis (the installer does).

## One-way audio / silence

```sh
asterisk -rx "pjsip show channelstats"
asterisk -rx "rtp show settings"
```

* Wrong RTP range or an unwired firewall: forward `10000-20000/udp`.
* NAT: set `MEDIA_ADDR=<public-ip>` (step 30) and make sure
  `external_media_address` / `external_signaling_address` are set.
* Codec mismatch - the trunk usually only offers G.711; keep
  `allow = !all,ulaw,alaw` consistent on both sides.

## DTMF is not detected

* `dtmf_mode = rfc4733` on the endpoint/trunk (the installer sets it).
* Trunks that pass RFC 2833 need `dtmf_mode=rfc4733` on both legs.
* In the flow, a `collect` action needs `max_digits` and a terminator (`#`);
  check the `interdigit_timeout` (default 4 s).
* Enable DEBUG logging and look for `DTMF '5' in menu main`.

## The dial action does nothing

* `dial.mode: dialplan` requires the `[openivr-dial]` context and the
  `OPENIVR_DIAL_TARGET` variable - both come from step 50.
* Check `asterisk -rx "dialplan show context openivr-dial"` and
  `grep OPENIVR data/logs/openivr.log`.
* If the target is not registered, `Dial()` returns `CHANUNAVAIL` and the flow
  falls back to the main menu (look for `DialResult` in the log).
* `dial.mode: originate` needs the endpoint to accept an ARI originate; watch
  for `Originated PJSIP/… as …` followed by `Bridged … <-> …`.

## Voicemail files are missing

* The ARI recording lands in `asterisk_spool` (`/var/spool/asterisk/recording`)
  as `<name>.<format>`; the IVR moves it into
  `paths` → `voicemail.dir`. Log line `recording file not found in Asterisk
  spool` means the spool path or `asterisk` group permissions are wrong:
  `chown asterisk:asterisk /var/spool/asterisk/recording`.
* `message too short` = the caller hung up immediately (see
  `voicemail.min_duration`).

## Voicemail mail never arrives

```sh
.venv/bin/python -m openivr builder   # open /smtp → "Save & test connection"
grep -n 'SMTP' data/logs/openivr.log
```

* Gmail and similar providers require an **app password** and sometimes
  `starttls: true` with port 587.
* `alerts_to` must not be empty - the builder refuses to enable SMTP without a
  recipient.
* The attachment is the stored WAV; keep it small or mail servers will reject it.

## Ports are blocked

```sh
sudo ufw status
sudo ./system/steps/20-firewall.sh
ss -lntup | grep -E '5060|8088|8090'
```

Builder port `8090` is loopback-only by design - use an SSH tunnel.

## `verify` fails on "flow validation"

The messages are literal: unreachable menus, unknown targets, too many options,
too deep nesting, missing prompts. `python -m openivr flow show` prints the
document; `python -m openivr flow tree` prints the menu tree.

## Nothing works after an Asterisk upgrade

```sh
asteriskctl reload     # keeps calls
sudo systemctl restart asterisk   # drops ARI; the IVR reconnects by itself
sudo ./system/install.sh --steps 10-asterisk.sh 30-ari.sh
```

## Getting more information

```sh
OPENIVR_LOG_LEVEL=DEBUG .venv/bin/python -m openivr run
asterisk -rx "core set verbose 5"
asterisk -rx "core set debug 5"
asterisk -rx "log set live yes"
```

Then compare with [resources.md](resources.md): the Asterisk community forum,
`/r/Asterisk` and the ARI explorer at `http://ari.asterisk.org` are the fastest
paths to an answer for low-level telephony problems.