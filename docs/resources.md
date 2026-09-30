# Resources

Collected from `asterisk-know-source.md` - the links that actually help when
an IVR misbehaves.

## Official Asterisk

* Documentation hub - <https://www.asterisk.org/community/documentation>
* Blog and release notes - <https://www.asterisk.org/blog>
* Community forum - <https://community.asterisk.org>
* Asterisk source - <https://github.com/asterisk/asterisk>

## ARI

* AsyncARI (used here) - <https://github.com/M-o-a-T/asyncari>
* aioari (older reference implementation) - <https://github.com/M-o-a-T/aioari>
* ARI API explorer - <http://ari.asterisk.org> (log in with
  `/etc/asterisk/ari.conf` credentials)

The ARI explorer is the fastest way to test an endpoint by hand: it shows the
exact Swagger schema for your Asterisk version.

## Communities

* r/Asterisk - <https://www.reddit.com/r/Asterisk/s/TF0HawEWNa>
* r/FreePBX - <https://www.reddit.com/r/FreePBX/s/FywQ45VyPJ>
* Discord (AI voice agents) - <https://discord.gg/PBZnNUpJW>
* Stack Overflow `asterisk` tag - <https://stackoverflow.com/questions/tagged/asterisk>

## Video

* VoIP Guys (Pascom) - <https://www.youtube.com/@pascomnet>
  (older, still the best free SIP/Asterisk walkthroughs)

## Books

* Asterisk: The Definitive Guide, 4th edition
  - <https://asterisk-service.com/downloads/Asterisk-1.4.pdf>
* More titles - <https://asterisk-service.com/en_US/page/asterisk-books>

## Reading Asterisk source

Two files answer most "why did the event not arrive" questions:

* `main/ari/resource_websockets.c` - which events ARI emits, and when
* `res/res_stasis.c` - how a channel enters, stays in, and leaves Stasis

## Debugging habits worth copying

From the notes in `asterisk-know-source.md`: generated ARI code often *looks*
right and still breaks the event flow. Break a call flow into small pieces, walk
the events one by one, and fix incrementally instead of rewriting.

Practical version of that advice:

1. reproduce with one menu, no actions
2. add DTMF, then timeouts, then voicemail, then dial
3. after each step, check `ari show channels`, the IVR log and the CDR