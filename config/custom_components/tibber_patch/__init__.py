"""Monkey-patch pytibber's watchdog to fix stale token on reconnect.

Workaround for https://github.com/home-assistant/core/issues/164527
The bug: TibberRT._watchdog() closes the session but doesn't set
self.sub_manager = None, so _create_sub_manager() reuses the old
transport with an expired OAuth token. The watchdog then retries
forever with "4403 Invalid token".

Fix: After closing the session in the watchdog, set sub_manager = None
so _create_sub_manager() builds a fresh transport with the current token.
"""

import logging

_LOGGER = logging.getLogger(__name__)

DOMAIN = "tibber_patch"


async def async_setup(hass, config):
    """Apply the pytibber watchdog patch."""
    try:
        import tibber.realtime as rt

        original_watchdog = rt.TibberRT._watchdog

        async def _patched_watchdog(self):
            """Patched watchdog that resets sub_manager before reconnect."""
            assert self.sub_manager is not None
            from tibber.websocket_transport import TibberWebsocketsTransport
            import datetime as dt
            import random
            import asyncio

            assert isinstance(self.sub_manager.transport, TibberWebsocketsTransport)

            await asyncio.sleep(60)

            _retry_count = 0
            next_test_all_homes_running = dt.datetime.now(tz=dt.UTC)
            while self._watchdog_running:
                await asyncio.sleep(5)
                if (
                    self.sub_manager is not None
                    and isinstance(self.sub_manager.transport, TibberWebsocketsTransport)
                    and self.sub_manager.transport.running
                    and self.sub_manager.transport.reconnect_at
                    > dt.datetime.now(tz=dt.UTC)
                    and dt.datetime.now(tz=dt.UTC) > next_test_all_homes_running
                ):
                    is_running = True
                    for home in self._homes:
                        if not home.rt_subscription_running:
                            is_running = False
                            next_test_all_homes_running = (
                                dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=60)
                            )
                            break
                    if is_running:
                        _retry_count = 0
                        continue

                if self.sub_manager is not None and isinstance(
                    self.sub_manager.transport, TibberWebsocketsTransport
                ):
                    self.sub_manager.transport.reconnect_at = (
                        dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=self._timeout)
                    )

                _LOGGER.error(
                    "Watchdog: Connection is down, attempting reconnect with fresh token"
                )

                try:
                    if self.session is not None:
                        await self.sub_manager.close_async()
                        self.session = None
                except Exception:
                    _LOGGER.exception("Error in watchdog close")

                # THIS IS THE FIX: reset sub_manager so _create_sub_manager()
                # builds a new transport with the current (refreshed) access token
                self.sub_manager = None

                if not self._watchdog_running:
                    return

                self._create_sub_manager()
                try:
                    self.session = await self.sub_manager.connect_async()
                    await self._resubscribe_homes()
                except Exception as err:
                    delay_seconds = min(
                        random.SystemRandom().randint(1, 30) + _retry_count**2,
                        5 * 60,
                    )
                    _retry_count += 1
                    _LOGGER.error(
                        "Error in watchdog connect, retrying in %s seconds, %s: %s",
                        delay_seconds,
                        _retry_count,
                        err,
                        exc_info=_retry_count > 1,
                    )
                    # Also reset sub_manager on failure so next retry gets fresh token
                    self.sub_manager = None
                    await asyncio.sleep(delay_seconds)
                else:
                    _LOGGER.debug("Watchdog: Reconnected successfully")
                    await asyncio.sleep(60)

        rt.TibberRT._watchdog = _patched_watchdog
        _LOGGER.info("tibber_patch: Successfully patched TibberRT._watchdog")

    except Exception:
        _LOGGER.exception("tibber_patch: Failed to apply patch")

    return True
