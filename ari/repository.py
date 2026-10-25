"""ARI Repositories mapping Asterisk REST API endpoints to typed Python methods."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .model import (
    Application,
    Bridge,
    Channel,
    DeviceState,
    Endpoint,
    LiveRecording,
    Mailbox,
    Playback,
    Sound,
    StoredRecording,
)

if TYPE_CHECKING:
    from .client import Client


class BaseRepository:
    """Base class for ARI endpoint repositories."""

    def __init__(self, client: Client, name: str) -> None:
        self.client = client
        self.name = name

    def __repr__(self) -> str:
        return f"<Repository {self.name}>"


class ChannelsRepository(BaseRepository):
    """Channels resource operations (/channels)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "channels")

    async def list(self) -> list[Channel]:
        """List all active channels in Asterisk."""
        data = await self.client.http.get("channels")
        return [self.client.get_channel(item["id"], item) for item in (data or [])]

    async def originate(
        self,
        endpoint: str,
        *,
        extension: str | None = None,
        context: str | None = None,
        priority: int | None = None,
        label: str | None = None,
        app: str | None = None,
        appArgs: str | list[str] | None = None,
        callerId: str | None = None,
        timeout: int | None = None,
        variables: dict[str, Any] | None = None,
        channelId: str | None = None,
        otherChannelId: str | None = None,
        originator: str | None = None,
        formats: str | None = None,
    ) -> Channel:
        """Create a new channel (originate)."""
        args_str = ",".join(appArgs) if isinstance(appArgs, (list, tuple)) else appArgs
        params: dict[str, Any] = {
            "endpoint": endpoint,
            "extension": extension,
            "context": context,
            "priority": priority,
            "label": label,
            "app": app,
            "appArgs": args_str,
            "callerId": callerId,
            "timeout": timeout,
            "channelId": channelId,
            "otherChannelId": otherChannelId,
            "originator": originator,
            "formats": formats,
        }
        json_data = {"variables": variables} if variables else None
        data = await self.client.http.post("channels", params=params, json_data=json_data)
        return self.client.get_channel(data["id"], data)

    async def originateWithId(
        self,
        channelId: str,
        endpoint: str,
        **kwargs: Any,
    ) -> Channel:
        """Create a new channel with a specific channel ID."""
        kwargs["channelId"] = channelId
        return await self.originate(endpoint, **kwargs)

    async def get(self, channelId: str) -> Channel:
        """Get channel details."""
        data = await self.client.http.get(f"channels/{channelId}")
        return self.client.get_channel(data["id"], data)

    async def hangup(self, channelId: str, reason: str | None = None) -> None:
        """Hang up a channel."""
        params = {"reason": reason} if reason else None
        await self.client.http.delete(f"channels/{channelId}", params=params)

    async def answer(self, channelId: str) -> None:
        """Answer a channel."""
        await self.client.http.post(f"channels/{channelId}/answer")

    async def ring(self, channelId: str) -> None:
        """Send ringing indication to a channel."""
        await self.client.http.post(f"channels/{channelId}/ring")

    async def ringStop(self, channelId: str) -> None:
        """Stop sending ringing indication to a channel."""
        await self.client.http.delete(f"channels/{channelId}/ring")

    async def mute(self, channelId: str, direction: str = "both") -> None:
        """Mute a channel."""
        await self.client.http.post(f"channels/{channelId}/mute", params={"direction": direction})

    async def unmute(self, channelId: str, direction: str = "both") -> None:
        """Unmute a channel."""
        await self.client.http.delete(f"channels/{channelId}/mute", params={"direction": direction})

    async def hold(self, channelId: str) -> None:
        """Hold a channel."""
        await self.client.http.post(f"channels/{channelId}/hold")

    async def stopHold(self, channelId: str) -> None:
        """Remove a channel from hold."""
        await self.client.http.delete(f"channels/{channelId}/hold")

    async def startMoh(self, channelId: str, mohClass: str | None = None) -> None:
        """Play music on hold to a channel."""
        params = {"mohClass": mohClass} if mohClass else None
        await self.client.http.post(f"channels/{channelId}/moh", params=params)

    async def stopMoh(self, channelId: str) -> None:
        """Stop playing music on hold to a channel."""
        await self.client.http.delete(f"channels/{channelId}/moh")

    async def startSilence(self, channelId: str) -> None:
        """Play silence to a channel."""
        await self.client.http.post(f"channels/{channelId}/silence")

    async def stopSilence(self, channelId: str) -> None:
        """Stop playing silence to a channel."""
        await self.client.http.delete(f"channels/{channelId}/silence")

    async def play(
        self,
        channelId: str,
        media: str,
        *,
        lang: str | None = None,
        offsetms: int | None = None,
        skipms: int | None = None,
        playbackId: str | None = None,
    ) -> Playback:
        """Play media to a channel."""
        params: dict[str, Any] = {
            "media": media,
            "lang": lang,
            "offsetms": offsetms,
            "skipms": skipms,
            "playbackId": playbackId,
        }
        data = await self.client.http.post(f"channels/{channelId}/play", params=params)
        return self.client.get_playback(data["id"], data)

    async def playWithId(
        self,
        channelId: str,
        playbackId: str,
        media: str,
        *,
        lang: str | None = None,
        offsetms: int | None = None,
        skipms: int | None = None,
    ) -> Playback:
        """Play media to a channel with a specified playback ID."""
        params: dict[str, Any] = {
            "media": media,
            "lang": lang,
            "offsetms": offsetms,
            "skipms": skipms,
        }
        data = await self.client.http.post(f"channels/{channelId}/play/{playbackId}", params=params)
        return self.client.get_playback(data["id"], data)

    async def record(
        self,
        channelId: str,
        name: str,
        format: str = "wav",
        *,
        maxDurationSeconds: int | None = None,
        maxSilenceSeconds: int | None = None,
        ifExists: str | None = None,
        beep: bool | None = None,
        terminateOn: str | None = None,
    ) -> LiveRecording:
        """Record audio from a channel."""
        params: dict[str, Any] = {
            "name": name,
            "format": format,
            "maxDurationSeconds": maxDurationSeconds,
            "maxSilenceSeconds": maxSilenceSeconds,
            "ifExists": ifExists,
            "beep": beep,
            "terminateOn": terminateOn,
        }
        data = await self.client.http.post(f"channels/{channelId}/record", params=params)
        return self.client.get_recording(data["name"], data)

    async def getChannelVar(self, channelId: str, variable: str) -> dict[str, Any]:
        """Get a variable on a channel."""
        return await self.client.http.get(f"channels/{channelId}/variable", params={"variable": variable})

    async def setChannelVar(self, channelId: str, variable: str, value: str) -> None:
        """Set a variable on a channel."""
        await self.client.http.post(
            f"channels/{channelId}/variable",
            params={"variable": variable, "value": value},
        )

    async def continueInDialplan(
        self,
        channelId: str,
        *,
        context: str | None = None,
        extension: str | None = None,
        priority: int | None = None,
        label: str | None = None,
    ) -> None:
        """Exit Stasis application and continue in dialplan."""
        params: dict[str, Any] = {
            "context": context,
            "extension": extension,
            "priority": priority,
            "label": label,
        }
        await self.client.http.post(f"channels/{channelId}/continue", params=params)

    async def redirect(self, channelId: str, endpoint: str) -> None:
        """Redirect channel to endpoint."""
        await self.client.http.post(f"channels/{channelId}/redirect", params={"endpoint": endpoint})

    async def sendDTMF(
        self,
        channelId: str,
        dtmf: str,
        *,
        before: int | None = None,
        between: int | None = None,
        duration: int | None = None,
        after: int | None = None,
    ) -> None:
        """Send DTMF to a channel."""
        params: dict[str, Any] = {
            "dtmf": dtmf,
            "before": before,
            "between": between,
            "duration": duration,
            "after": after,
        }
        await self.client.http.post(f"channels/{channelId}/dtmf", params=params)

    async def dial(
        self,
        channelId: str,
        *,
        caller: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """Dial created channel."""
        params: dict[str, Any] = {"caller": caller, "timeout": timeout}
        await self.client.http.post(f"channels/{channelId}/dial", params=params)

    async def snoopChannel(
        self,
        channelId: str,
        app: str,
        *,
        spy: str | None = None,
        whisper: str | None = None,
        appArgs: str | None = None,
        snoopId: str | None = None,
    ) -> Channel:
        """Start snooping on a channel."""
        params: dict[str, Any] = {
            "app": app,
            "spy": spy,
            "whisper": whisper,
            "appArgs": appArgs,
            "snoopId": snoopId,
        }
        data = await self.client.http.post(f"channels/{channelId}/snoop", params=params)
        return self.client.get_channel(data["id"], data)

    async def snoopChannelWithId(
        self,
        channelId: str,
        snoopId: str,
        app: str,
        **kwargs: Any,
    ) -> Channel:
        kwargs["snoopId"] = snoopId
        return await self.snoopChannel(channelId, app, **kwargs)

    async def externalMedia(
        self,
        app: str,
        external_host: str,
        format: str = "slin16",
        *,
        channelId: str | None = None,
        encapsulation: str = "rtp",
        transport: str = "udp",
        connection_type: str = "client",
        direction: str = "both",
        data: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> Channel:
        """Create an external media channel for RTP streaming."""
        params: dict[str, Any] = {
            "app": app,
            "external_host": external_host,
            "format": format,
            "channelId": channelId,
            "encapsulation": encapsulation,
            "transport": transport,
            "connection_type": connection_type,
            "direction": direction,
            "data": data,
        }
        json_data = {"variables": variables} if variables else None
        res = await self.client.http.post("channels/externalMedia", params=params, json_data=json_data)
        return self.client.get_channel(res["id"], res)


class BridgesRepository(BaseRepository):
    """Bridges resource operations (/bridges)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "bridges")

    async def list(self) -> list[Bridge]:
        """List active bridges."""
        data = await self.client.http.get("bridges")
        return [self.client.get_bridge(item["id"], item) for item in (data or [])]

    async def create(
        self,
        *,
        type: str | None = None,
        bridgeId: str | None = None,
        name: str | None = None,
    ) -> Bridge:
        """Create a new bridge."""
        params: dict[str, Any] = {
            "type": type,
            "bridgeId": bridgeId,
            "name": name,
        }
        data = await self.client.http.post("bridges", params=params)
        return self.client.get_bridge(data["id"], data)

    async def createWithId(
        self,
        bridgeId: str,
        *,
        type: str | None = None,
        name: str | None = None,
    ) -> Bridge:
        """Create a new bridge with specific bridge ID."""
        params: dict[str, Any] = {"type": type, "name": name}
        data = await self.client.http.post(f"bridges/{bridgeId}", params=params)
        return self.client.get_bridge(data["id"], data)

    async def get(self, bridgeId: str) -> Bridge:
        """Get bridge details."""
        data = await self.client.http.get(f"bridges/{bridgeId}")
        return self.client.get_bridge(data["id"], data)

    async def destroy(self, bridgeId: str) -> None:
        """Destroy a bridge."""
        await self.client.http.delete(f"bridges/{bridgeId}")

    async def addChannel(
        self,
        bridgeId: str,
        channel: str | list[str],
        *,
        role: str | None = None,
        absorbDTMF: bool | None = None,
        mute: bool | None = None,
        inhibitConnectedLineUpdates: bool | None = None,
    ) -> None:
        """Add channels to a bridge."""
        chan_str = ",".join(channel) if isinstance(channel, (list, tuple)) else str(channel)
        params: dict[str, Any] = {
            "channel": chan_str,
            "role": role,
            "absorbDTMF": absorbDTMF,
            "mute": mute,
            "inhibitConnectedLineUpdates": inhibitConnectedLineUpdates,
        }
        await self.client.http.post(f"bridges/{bridgeId}/addChannel", params=params)

    async def removeChannel(self, bridgeId: str, channel: str | list[str]) -> None:
        """Remove channels from a bridge."""
        chan_str = ",".join(channel) if isinstance(channel, (list, tuple)) else str(channel)
        await self.client.http.post(f"bridges/{bridgeId}/removeChannel", params={"channel": chan_str})

    async def setVideoSource(self, bridgeId: str, channelId: str) -> None:
        """Set video source channel for a bridge."""
        await self.client.http.post(f"bridges/{bridgeId}/videoSource/{channelId}")

    async def clearVideoSource(self, bridgeId: str) -> None:
        """Clear video source for a bridge."""
        await self.client.http.delete(f"bridges/{bridgeId}/videoSource")

    async def startMoh(self, bridgeId: str, mohClass: str | None = None) -> None:
        """Play music on hold into a bridge."""
        params = {"mohClass": mohClass} if mohClass else None
        await self.client.http.post(f"bridges/{bridgeId}/moh", params=params)

    async def stopMoh(self, bridgeId: str) -> None:
        """Stop music on hold on a bridge."""
        await self.client.http.delete(f"bridges/{bridgeId}/moh")

    async def play(
        self,
        bridgeId: str,
        media: str,
        *,
        lang: str | None = None,
        offsetms: int | None = None,
        skipms: int | None = None,
        playbackId: str | None = None,
    ) -> Playback:
        """Play media into a bridge."""
        params: dict[str, Any] = {
            "media": media,
            "lang": lang,
            "offsetms": offsetms,
            "skipms": skipms,
            "playbackId": playbackId,
        }
        data = await self.client.http.post(f"bridges/{bridgeId}/play", params=params)
        return self.client.get_playback(data["id"], data)

    async def playWithId(
        self,
        bridgeId: str,
        playbackId: str,
        media: str,
        **kwargs: Any,
    ) -> Playback:
        kwargs["playbackId"] = playbackId
        return await self.play(bridgeId, media, **kwargs)

    async def record(
        self,
        bridgeId: str,
        name: str,
        format: str = "wav",
        *,
        maxDurationSeconds: int | None = None,
        maxSilenceSeconds: int | None = None,
        ifExists: str | None = None,
        beep: bool | None = None,
        terminateOn: str | None = None,
    ) -> LiveRecording:
        """Record audio from a bridge."""
        params: dict[str, Any] = {
            "name": name,
            "format": format,
            "maxDurationSeconds": maxDurationSeconds,
            "maxSilenceSeconds": maxSilenceSeconds,
            "ifExists": ifExists,
            "beep": beep,
            "terminateOn": terminateOn,
        }
        data = await self.client.http.post(f"bridges/{bridgeId}/record", params=params)
        return self.client.get_recording(data["name"], data)


class PlaybacksRepository(BaseRepository):
    """Playbacks resource operations (/playbacks)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "playbacks")

    async def get(self, playbackId: str) -> Playback:
        """Get playback details."""
        data = await self.client.http.get(f"playbacks/{playbackId}")
        return self.client.get_playback(data["id"], data)

    async def stop(self, playbackId: str) -> None:
        """Stop a playback."""
        await self.client.http.delete(f"playbacks/{playbackId}")

    async def control(self, playbackId: str, operation: str) -> None:
        """Control playback (pause, unpause, reverse, forward, restart)."""
        await self.client.http.post(f"playbacks/{playbackId}/control", params={"operation": operation})


class RecordingsRepository(BaseRepository):
    """Recordings resource operations (/recordings)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "recordings")

    async def listStored(self) -> list[StoredRecording]:
        """List stored recordings."""
        data = await self.client.http.get("recordings/stored")
        return [self.client.get_stored_recording(item["name"], item) for item in (data or [])]

    async def getStored(self, recordingName: str) -> StoredRecording:
        """Get details of a stored recording."""
        data = await self.client.http.get(f"recordings/stored/{recordingName}")
        return self.client.get_stored_recording(data["name"], data)

    async def deleteStored(self, recordingName: str) -> None:
        """Delete a stored recording."""
        await self.client.http.delete(f"recordings/stored/{recordingName}")

    async def getStoredFile(self, recordingName: str) -> bytes:
        """Get stored recording audio file content."""
        return await self.client.http.get(f"recordings/stored/{recordingName}/file")

    async def copyStored(self, recordingName: str, destinationRecordingName: str) -> StoredRecording:
        """Copy a stored recording to a new name."""
        data = await self.client.http.post(
            f"recordings/stored/{recordingName}/copy",
            params={"destinationRecordingName": destinationRecordingName},
        )
        return self.client.get_stored_recording(data["name"], data)

    async def getLive(self, recordingName: str) -> LiveRecording:
        """Get live recording status."""
        data = await self.client.http.get(f"recordings/live/{recordingName}")
        return self.client.get_recording(data["name"], data)

    async def cancel(self, recordingName: str) -> None:
        """Cancel live recording and discard file."""
        await self.client.http.delete(f"recordings/live/{recordingName}")

    async def stop(self, recordingName: str) -> None:
        """Stop live recording and store file."""
        await self.client.http.post(f"recordings/live/{recordingName}/stop")

    async def pause(self, recordingName: str) -> None:
        """Pause live recording."""
        await self.client.http.post(f"recordings/live/{recordingName}/pause")

    async def unpause(self, recordingName: str) -> None:
        """Resume live recording."""
        await self.client.http.delete(f"recordings/live/{recordingName}/pause")

    async def mute(self, recordingName: str) -> None:
        """Mute live recording."""
        await self.client.http.post(f"recordings/live/{recordingName}/mute")

    async def unmute(self, recordingName: str) -> None:
        """Unmute live recording."""
        await self.client.http.delete(f"recordings/live/{recordingName}/mute")


class AsteriskRepository(BaseRepository):
    """Asterisk resource operations (/asterisk)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "asterisk")

    async def getInfo(self, only: str | None = None) -> dict[str, Any]:
        """Get Asterisk system information."""
        params = {"only": only} if only else None
        return await self.client.http.get("asterisk/info", params=params)

    async def ping(self) -> dict[str, Any]:
        """Send ping to Asterisk."""
        return await self.client.http.get("asterisk/ping")

    async def getGlobalVar(self, variable: str) -> dict[str, Any]:
        """Get global variable value."""
        return await self.client.http.get("asterisk/variable", params={"variable": variable})

    async def setGlobalVar(self, variable: str, value: str) -> None:
        """Set global variable value."""
        await self.client.http.post("asterisk/variable", params={"variable": variable, "value": value})

    async def listModules(self) -> list[dict[str, Any]]:
        """List loaded Asterisk modules."""
        return await self.client.http.get("asterisk/modules")

    async def getModule(self, moduleName: str) -> dict[str, Any]:
        """Get module details."""
        return await self.client.http.get(f"asterisk/modules/{moduleName}")

    async def loadModule(self, moduleName: str) -> None:
        """Load module."""
        await self.client.http.post(f"asterisk/modules/{moduleName}")

    async def unloadModule(self, moduleName: str) -> None:
        """Unload module."""
        await self.client.http.delete(f"asterisk/modules/{moduleName}")

    async def reloadModule(self, moduleName: str) -> None:
        """Reload module."""
        await self.client.http.put(f"asterisk/modules/{moduleName}")

    async def listLogChannels(self) -> list[dict[str, Any]]:
        """List Asterisk logging channels."""
        return await self.client.http.get("asterisk/logging")

    async def addLog(self, logChannelName: str, configuration: str) -> None:
        """Add log channel."""
        await self.client.http.post(f"asterisk/logging/{logChannelName}", params={"configuration": configuration})

    async def rotateLog(self, logChannelName: str) -> None:
        """Rotate log channel."""
        await self.client.http.put(f"asterisk/logging/{logChannelName}/rotate")

    async def deleteLog(self, logChannelName: str) -> None:
        """Delete log channel."""
        await self.client.http.delete(f"asterisk/logging/{logChannelName}")


class SoundsRepository(BaseRepository):
    """Sounds resource operations (/sounds)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "sounds")

    async def list(self, *, lang: str | None = None, format: str | None = None) -> list[Sound]:
        """List sounds."""
        params: dict[str, Any] = {"lang": lang, "format": format}
        data = await self.client.http.get("sounds", params=params)
        return [Sound(self.client, id=item.get("id"), json=item) for item in (data or [])]

    async def get(self, soundId: str) -> Sound:
        """Get sound details."""
        data = await self.client.http.get(f"sounds/{soundId}")
        return Sound(self.client, id=data.get("id"), json=data)


class ApplicationsRepository(BaseRepository):
    """Applications resource operations (/applications)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "applications")

    async def list(self) -> list[Application]:
        """List registered applications."""
        data = await self.client.http.get("applications")
        return [Application(self.client, id=item.get("name"), json=item) for item in (data or [])]

    async def get(self, applicationName: str) -> Application:
        """Get application details."""
        data = await self.client.http.get(f"applications/{applicationName}")
        return Application(self.client, id=data.get("name"), json=data)

    async def subscribe(self, applicationName: str, eventSource: str | list[str]) -> Application:
        """Subscribe application to event source."""
        source_str = ",".join(eventSource) if isinstance(eventSource, (list, tuple)) else str(eventSource)
        data = await self.client.http.post(
            f"applications/{applicationName}/subscription",
            params={"eventSource": source_str},
        )
        return Application(self.client, id=data.get("name"), json=data)

    async def unsubscribe(self, applicationName: str, eventSource: str | list[str]) -> Application:
        """Unsubscribe application from event source."""
        source_str = ",".join(eventSource) if isinstance(eventSource, (list, tuple)) else str(eventSource)
        data = await self.client.http.delete(
            f"applications/{applicationName}/subscription",
            params={"eventSource": source_str},
        )
        return Application(self.client, id=data.get("name"), json=data)


class EndpointsRepository(BaseRepository):
    """Endpoints resource operations (/endpoints)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "endpoints")

    async def list(self) -> list[Endpoint]:
        """List endpoints."""
        data = await self.client.http.get("endpoints")
        return [Endpoint(self.client, json=item) for item in (data or [])]

    async def listByTech(self, tech: str) -> list[Endpoint]:
        """List endpoints by technology."""
        data = await self.client.http.get(f"endpoints/{tech}")
        return [Endpoint(self.client, json=item) for item in (data or [])]

    async def get(self, tech: str, resource: str) -> Endpoint:
        """Get endpoint details."""
        data = await self.client.http.get(f"endpoints/{tech}/{resource}")
        return Endpoint(self.client, json=data)

    async def sendMessage(
        self,
        to: str,
        from_: str,
        body: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        """Send message to endpoint."""
        params: dict[str, Any] = {"to": to, "from": from_, "body": body}
        json_data = {"variables": variables} if variables else None
        await self.client.http.put("endpoints/sendMessage", params=params, json_data=json_data)


class DeviceStatesRepository(BaseRepository):
    """DeviceStates resource operations (/deviceStates)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "deviceStates")

    async def list(self) -> list[DeviceState]:
        """List device states."""
        data = await self.client.http.get("deviceStates")
        return [DeviceState(self.client, id=item.get("name"), json=item) for item in (data or [])]

    async def get(self, deviceName: str) -> DeviceState:
        """Get device state."""
        data = await self.client.http.get(f"deviceStates/{deviceName}")
        return DeviceState(self.client, id=data.get("name"), json=data)

    async def update(self, deviceName: str, deviceState: str) -> None:
        """Update device state."""
        await self.client.http.put(f"deviceStates/{deviceName}", params={"deviceState": deviceState})

    async def delete(self, deviceName: str) -> None:
        """Delete device state."""
        await self.client.http.delete(f"deviceStates/{deviceName}")


class MailboxesRepository(BaseRepository):
    """Mailboxes resource operations (/mailboxes)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "mailboxes")

    async def list(self) -> list[Mailbox]:
        """List mailboxes."""
        data = await self.client.http.get("mailboxes")
        return [Mailbox(self.client, id=item.get("name"), json=item) for item in (data or [])]

    async def get(self, mailboxName: str) -> Mailbox:
        """Get mailbox details."""
        data = await self.client.http.get(f"mailboxes/{mailboxName}")
        return Mailbox(self.client, id=data.get("name"), json=data)

    async def update(self, mailboxName: str, oldMessages: int, newMessages: int) -> None:
        """Update mailbox message count."""
        params = {"oldMessages": oldMessages, "newMessages": newMessages}
        await self.client.http.put(f"mailboxes/{mailboxName}", params=params)

    async def destroy(self, mailboxName: str) -> None:
        """Destroy mailbox."""
        await self.client.http.delete(f"mailboxes/{mailboxName}")


class EventsRepository(BaseRepository):
    """Events resource operations (/events)."""

    def __init__(self, client: Client) -> None:
        super().__init__(client, "events")

    async def userEvent(
        self,
        eventName: str,
        application: str,
        *,
        source: str | list[str] | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        """Generate a user-defined event in Asterisk."""
        src_str = ",".join(source) if isinstance(source, (list, tuple)) else source
        params = {"application": application, "source": src_str}
        json_data = {"variables": variables} if variables else None
        await self.client.http.post(f"events/user/{eventName}", params=params, json_data=json_data)
