"""Outgoing calls: ARI originate + bridge (LEGACY).

.. warning::
   This module is LEGACY. The supported outbound path is
   ``dial.mode: dialplan`` which uses ``channel.continueInDialplan()`` into the
   ``[openivr-dial]`` context (see extensions.conf). This module is retained as
   a fallback and for the ``openivr originate --to ...`` CLI escape hatch.

``plan.md`` prefers handing a ``dial`` action to the dialplan so the CDR stays
clean (``dial.mode: dialplan``).  This module implements the alternative:
``dial.mode: originate`` – originate a second leg into our own Stasis app and
bridge it against the inbound channel.  It is also used by the CLI
``openivr originate --to PJSIP/1001`` helper.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import anyio

log = logging.getLogger(__name__)

DIALED_ARG = "dialed"


@dataclass(slots=True)
class OriginateResult:
    ok: bool
    endpoint: str
    channel_id: str = ""
    duration: float = 0.0
    reason: str = ""


def is_dialed_leg(event) -> bool:
    """True when a StasisStart belongs to an originated leg of ours."""
    args = [str(a) for a in (getattr(event, "args", None) or [])]
    return DIALED_ARG in args


async def wait_for_stasis_start(client, channel_id: str, timeout: float = 30.0):
    """Wait until the given channel reports StasisStart."""

    async def waiter() -> object:
        async with client.on_channel_event("StasisStart") as listener:
            async for _objs, event in listener:
                channel = getattr(event, "channel", None)
                if channel is not None and channel.id == channel_id:
                    return event
        return None

    with anyio.move_on_after(timeout):
        return await waiter()
    return None


async def originate_and_bridge(
    client,
    incoming,
    *,
    endpoint: str,
    app: str,
    caller_id: str | None = None,
    timeout: float = 30.0,
    variables: dict[str, str] | None = None,
    guard: float = 900.0,
) -> OriginateResult:
    """Originate *endpoint* into *app* and bridge it with *incoming*.

    Both listeners are registered *before* the originate request so neither the
    ``StasisStart`` of the new leg nor an early ``StasisEnd`` (busy, no answer,
    carrier reject) can slip through the gap between the HTTP call and the
    subscription.
    """
    token = uuid.uuid4().hex[:8]
    if not caller_id:
        caller_id = incoming.caller_id_num or None
    ended = anyio.Event()
    bridge: object | None = None
    outgoing = None

    async with anyio.create_task_group() as tg:
        async with (
            client.on_channel_event("StasisStart") as start_listener,
            client.on_channel_event("StasisEnd") as end_listener,
        ):
            tg.start_soon(_answer_dialed, start_listener, token)
            tg.start_soon(_end_dialed, end_listener, token, ended)
            try:
                outgoing = await client.channels.originate(
                    endpoint=endpoint,
                    app=app,
                    appArgs=[DIALED_ARG, token],
                    timeout=int(timeout),
                    callerId=caller_id or "openivr",
                    variables=variables or {},
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Originate to %s failed: %s", endpoint, exc)
                outgoing = None
                result = OriginateResult(ok=False, endpoint=endpoint, reason=str(exc))

            if outgoing is not None:
                log.info("Originated %s as %s (token %s)", endpoint, outgoing.id, token)
                started = anyio.current_time()
                try:
                    bridge = await client.bridges.create(
                        type="mixing", bridgeId=f"openivr-bridge-{token}"
                    )
                    await bridge.addChannel(channel=incoming.id)
                    await bridge.addChannel(channel=outgoing.id)
                    log.info("Bridged %s <-> %s (%s)", incoming.id, outgoing.id, bridge.id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Bridge setup for %s failed: %s", endpoint, exc)
                    ended.set()
                else:
                    with anyio.move_on_after(guard):
                        await ended.wait()
                try:
                    with anyio.move_on_after(2, shield=True):
                        await outgoing.hangup()
                except Exception:  # noqa: BLE001,S110 - best effort cleanup
                    pass
                result = OriginateResult(
                    ok=True,
                    endpoint=endpoint,
                    channel_id=outgoing.id,
                    duration=round(anyio.current_time() - started, 2),
                )

            tg.cancel_scope.cancel()
            try:
                with anyio.move_on_after(2, shield=True):
                    await incoming.hangup()
            except Exception:  # noqa: BLE001,S110 - best effort cleanup
                pass
            if bridge is not None:
                with anyio.move_on_after(2, shield=True):
                    await bridge.destroy()
    return result


async def _answer_dialed(listener, token: str) -> None:
    """Answer the leg we just originated as soon as it enters Stasis."""
    async for _objs, event in listener:
        if token not in [str(a) for a in (getattr(event, "args", None) or [])]:
            continue
        channel = getattr(event, "channel", None)
        if channel is None:
            continue
        try:
            with anyio.move_on_after(15, shield=True):
                await channel.answer()
        except Exception as exc:  # noqa: BLE001
            log.warning("Answer outgoing %s failed: %s", channel.id, exc)
        return


async def _end_dialed(listener, token: str, ended: anyio.Event) -> None:
    """Flag the end of the dialed leg (answered or not)."""
    async for _objs, event in listener:
        if token not in [str(a) for a in (getattr(event, "args", None) or [])]:
            continue
        ended.set()
        return


async def originate_simple(
    client,
    *,
    endpoint: str,
    app: str,
    caller_id: str | None = None,
    timeout: float = 30.0,
    variables: dict[str, str] | None = None,
) -> OriginateResult:
    """Originate a call and answer it (no bridge); used by the CLI helper."""
    token = uuid.uuid4().hex[:8]
    try:
        channel = await client.channels.originate(
            endpoint=endpoint,
            app=app,
            appArgs=[DIALED_ARG, token],
            timeout=int(timeout),
            callerId=caller_id or "openivr",
            variables=variables or {},
        )
    except Exception as exc:  # noqa: BLE001
        return OriginateResult(ok=False, endpoint=endpoint, reason=str(exc))
    try:
        with anyio.move_on_after(20, shield=True):
            await channel.answer()
    except Exception as exc:  # noqa: BLE001
        log.warning("Answer failed for %s: %s", channel.id, exc)
    return OriginateResult(ok=True, endpoint=endpoint, channel_id=channel.id)