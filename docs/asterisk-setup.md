# Asterisk setup for the IVR

What the installer writes, and why. All paths assume `/etc/asterisk`.

## Files the installer owns

| file | written by | purpose |
|------|-----------|---------|
| `ari.conf` | `30-ari.sh` | ARI user/password for the IVR |
| `http.conf` | `30-ari.sh` | HTTP listener (`127.0.0.1:8088` by default) |
| `rtp.conf` | `30-ari.sh` | RTP port range `10000-20000` |
| `modules.conf.d/openivr.conf` | `30-ari.sh` | loads pjsip, websocket, ari, stasis |
| `pjsip-trunk.conf` | `40-trunk.sh` | optional provider trunk |
| `pjsip-extensions.conf` | `50-extensions.sh` | local endpoints (6001…) |
| `openivr.conf` | `50-extensions.sh` | IVR dialplan contexts |

## ARI

```ini
; /etc/asterisk/ari.conf
[general]
enabled = yes
pretty = yes

[user]
user = openivr
password = <generated>
format = wav,ulaw,alaw
read_only = no
```

The IVR connects to `http://127.0.0.1:8088/ari`. Keep ARI on loopback (or a
firewalled management network); anyone with those credentials can hang up your
calls.

Test by hand:

```sh
curl -u openivr:PASSWORD http://127.0.0.1:8088/ari/asterisk/info
# or open http://ari.asterisk.org and log in with the same credentials
```

## Dialplan

Two contexts matter:

```ini
[openivr-ivr]
exten => s,1,Answer()
  same => n,Stasis(openivr,s,${CALLERID(num)})
  same => n,Hangup()

[openivr-dial]
exten => s,1,GotoIf($["${OPENIVR_DIAL_TARGET}" != ""]?dial)
  same => n,Playback(please-leave-a-message-after-the-tone)
  same => n,Hangup()
  same => n(dial),Dial(${OPENIVR_DIAL_TARGET},45)
  same => n,Goto(openivr-ivr,s,1)
```

* **`openivr-ivr`** is the inbound entry point - point your carrier here.
* **`openivr-dial`** receives `dial` actions from the IVR. The Python side sets
  `OPENIVR_DIAL_TARGET` (plus `OPENIVR_CALL_ID`, `OPENIVR_MENU`,
  `OPENIVR_DIGITS`) and calls `continueInDialplan`. This is what keeps the CDR
  clean: Asterisk performs the `Dial()`, so `MixMonitor`, CDRs and recording all
  behave exactly as if the IVR were not involved.

Inbound calls from a trunk land in the context you chose in step 40
(`from-trunk` by default), which answers and enters Stasis.

## Extensions

`50-extensions.sh` creates `PJSIP` endpoints `6001…` in the
`openivr-internal` context, so extension-to-extension calls work. Point a
softphone (Linphone, Zoiper, MicroSIP, pjsip CLI) at the server: no
authentication is required for the local endpoints the script writes; add
`authenticate=yes` sections if your setup needs them.

## Recording

Step 50 asks whether human-to-human calls should be recorded and, if so, adds
`MixMonitor(${UNIQUEID}.wav,b)` to `from-trunk`, `openivr-ivr` and
`openivr-dial` (files land in `/var/spool/asterisk/monitor`, pruned by the
cron job from step 70).

Recording of the **IVR leg itself** is a separate runtime switch:

```yaml
record:
  ivr_leg: true   # ARI recording, paused while prompts play
```

Voicemail always uses `channel.record()` and is moved to
`data/recordings/voicemail/`.

## RTP and NAT

`rtp.conf` gets `rtpstart`/`rtpend` and, when you pass `MEDIA_ADDR=`:

```sh
sudo MEDIA_ADDR=1.2.3.4 ./system/steps/30-ari.sh
```

which sets `external_media_address` and `external_signaling_address`. Forward
`5060/udp` and `10000-20000/udp` to the Asterisk host.

## Quick diagnostics

```sh
asterisk -rx "pjsip show endpoints"
asterisk -rx "pjsip show registrations"      # trunk registration status
asterisk -rx "dialplan show context openivr-dial"
asterisk -rx "core show application Stasis"
asterisk -rx "ari show channels"
asterisk -rx "agi debug"                     # not needed for ARI, but noisy
```