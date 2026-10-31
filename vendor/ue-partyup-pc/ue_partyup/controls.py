"""Read-probed settings adapter selection."""
from contextlib import asynccontextmanager
from .session import Session, NoReply


@asynccontextmanager
async def settings_session(speaker, experimental=False, group_host=None):
    if speaker.experimental and not experimental:
        raise ValueError('Enable experimental compatibility for this speaker.')
    if group_host is not None and speaker.address != group_host:
        from .controller import Speaker
        from .groups import broadcasting, XUP
        # Never contact the guest until the main confirms the group is off.
        async with Session(Speaker(group_host)) as host_session:
            index = await host_session.feature(XUP)
            state = await host_session.request(index, 1, read=True)
            if broadcasting(state):
                raise RuntimeError('Guest controls are paused while PartyUp is active to protect playback. Stop PartyUp explicitly before reading or changing a guest.')
    modern = Session(speaker)
    try:
        await modern.connect()
    except NoReply:
        if not speaker.experimental:
            raise
    else:
        from .settings import Settings
        try:
            yield Settings(modern)
        finally:
            modern.close()
        return
    from .legacy import LegacySession, LegacySettings
    async with LegacySession(speaker, experimental=experimental) as session:
        probe = await session.probe()
        if not probe.broadcast_available or probe.profile == 'NO_GROUP':
            raise RuntimeError('No known legacy PartyUp profile was confirmed.')
        yield LegacySettings(session)
