# Prompt audio for the IVR

Everything in this folder is played back by Asterisk, so the files have to match
what your telephony provider (and Asterisk's codecs) expect.

## Required format

| property | value |
|----------|-------|
| container | WAV (`.wav`) |
| sample rate | 8000 Hz for plain G.711 calls, 16000 Hz for wideband |
| channels | 1 (mono) |
| bit depth | 16 bit, signed PCM (linear) |
| codec | u-law / A-law are what SIP carries; keep mono 8/16 kHz 16-bit |

Never ship stereo, MP3 or 44.1 kHz files: Asterisk has to resample them on the
fly, which adds noticeable delay and, for menu prompts, breaks barge-in.

## Convert an existing recording

```sh
# 8 kHz mono, the safe default for SIP/ISDN
sox input.mp3 -r 8000 -c 1 -b 16 -t wav welcome.wav

# 16 kHz mono for wideband SIP trunks
sox input.mp3 -r 16000 -c 1 -b 16 -t wav welcome.wav

# trim the silence around the phrase before converting
sox input.wav silence.wav silence 1 0.3 1% -1 0.3 1% : newfile
sox silence.wav -r 8000 -c 1 -b 16 welcome.wav
```

`ffmpeg` works too:

```sh
ffmpeg -i input.mp3 -ar 8000 -ac 1 -c:a pcm_s16le welcome.wav
```

## Record it yourself

```sh
# arecord writes exactly what Asterisk wants
arecord -f S16_LE -r 8000 -c 1 -d 5 welcome.wav     # press Ctrl-C after speaking
```

Useful free tools:

* **Audacity** - Record, then *Track ▸ Resample Project* to 8000 Hz and
  *Track ▸ Mono*.
* **espeak-ng** - instant robotic prompts for testing:
  `espeak-ng -w welcome.wav "Welcome to openivr"` then
  `sox welcome.wav -r 8000 -c 1 -b 16 welcome.wav`.
* **Piper / Coqui TTS** - natural-sounding prompts, offline, free:
  `piper --model en_US-lessac-medium.onnx -f main-menu.wav`
  (convert to 8 kHz mono afterwards).

## Naming rules

Prompt names are referenced from the flow (`data/ivr_flow.json`) and resolved as
`sound:custom/<name>`, because `run_ivr.sh` symlinks/copies this folder into
Asterisk's sounds directory.

* letters, digits, `.`, `_`, `-` only
* no spaces, no slashes, no leading dot
* `.wav` extension only (the builder rejects other formats)

Example names used by the shipped sample flow:

| prompt | content |
|--------|---------|
| `welcome` | "Thank you for calling…" |
| `main-menu` | "For sales press 1, for support press 2…" |
| `support-menu` | support sub-menu prompt |
| `timeout-msg` | "I didn't get that, please press a key" |
| `invalid-msg` | "That option is not valid" |
| `vm-support` | "You've reached support voicemail, please leave a message" |
| `enter-account` | "Please enter your account number and press pound" |
| `goodbye` | "Thank you, goodbye" |

## Sizing

Keep every prompt under ~10 seconds; menus are played with barge-in, so a long
prompt only delays the first digit.

## Files here are not committed

`.gitignore` keeps `data/sounds/*` out of the repository (only this README is
tracked), so your recordings never leak into git. Generate a test set with:

```sh
.venv/bin/python scripts/make_sounds.py --out data/sounds
```