import asyncio
import contextlib
import datetime
import logging
import typing

import croniter
import pytz

logger = logging.getLogger(__name__)


class TimeStream(object):
    def __init__(self, timespec, now):
        self._spec = timespec
        self._iter = croniter.croniter(timespec, now)
        self._special_window = datetime.timedelta(minutes=10)

        self._prev = self._iter.get_prev(datetime.datetime)
        self._next = self._iter.get_next(datetime.datetime)

    def is_between(self, now: datetime.datetime) -> bool:

        while now > self._next:
            self._prev = self._next
            self._next = self._iter.get_next(datetime.datetime)

        while now < self._prev:
            self._next = self._prev
            self._prev = self._iter.get_prev(datetime.datetime)

        if now - self._special_window < self._prev:
            logger.debug("Less than 10 minutes since %s -> increase interval", self._prev.isoformat())
            return True

        if now + self._special_window > self._next:
            logger.debug("Less than 10 minutes to %s -> increase interval", self._next.isoformat())
            return True

        return False


class Timer(object):
    def __init__(self, interval: float = 5 * 60,
                 special_times: typing.List[str] = None,
                 time_zone: str = 'CET'):
        self._interval = interval
        self._special_times = []  # type: typing.List[TimeStream]
        self._time_zone = pytz.timezone(time_zone)

        self._sleep_coro = None # type: asyncio.Future

        now = self._now()

        for line in special_times:
            if not croniter.croniter.is_valid(line):
                logger.warning("Special time definition '%s' not valid. Ignoring!")
            else:
                self._special_times.append(TimeStream(line, now))

    def _now(self) -> datetime.datetime:
        utc_now = pytz.utc.localize(datetime.datetime.utcnow())
        return utc_now.astimezone(self._time_zone)

    def _wait_time(self, now):
        for special in self._special_times:
            if special.is_between(now):
                return 0.5
        return self._interval

    @contextlib.asynccontextmanager
    async def timed(self):
        start = self._now()

        yield

        end = self._now()
        elapsed = (end - start).total_seconds()
        sleep = max(0.0, self._wait_time(end) - elapsed)
        logger.debug("Booking run completed in %0.2f seconds - now sleep for %0.2f seconds", elapsed, sleep)
        self._sleep_coro = asyncio.ensure_future(asyncio.sleep(sleep))
        try:
            await self._sleep_coro
        except asyncio.CancelledError:
            pass
        finally:
            self._sleep_coro = None

    def cancel(self):
        if self._sleep_coro is not None:
            self._sleep_coro.cancel()


if __name__ == '__main__':
    t = Timer(20, ["0 0 * * *"])
    z = pytz.timezone("CET")
    d = datetime.datetime(2019, 1, 17, 0, 0, 0, 0)

    for i in range(40):
        tm = d + datetime.timedelta(minutes=i-20)

        inte = t._wait_time(z.localize(tm))
        print(tm.isoformat(), inte)
