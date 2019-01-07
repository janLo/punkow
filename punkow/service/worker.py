import asyncio
import concurrent.futures
import datetime
import functools
import logging
import typing

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from . import model
from .. import scraper

logger = logging.getLogger(__name__)


class _WorkerRequest(typing.NamedTuple):
    id: int
    name: str
    email: str
    target: str


class RequestQueue(object):
    def __init__(self):
        self._targets = []
        self._requests = []

    def enqueue(self, req: model.Request):
        if req.target not in self._requests:
            self._targets.append(req.target)

        self._requests.append(
            _WorkerRequest(req.id, req.data.name, req.data.email, req.target))

    def iterate(self):
        for item in self._requests:
            yield item.target, [item]

    def is_empty(self):
        return len(self._targets) == 0

    @property
    def target_cnt(self):
        return len(self._targets)

    @property
    def request_cnt(self):
        return len(self._requests)


def _book(target: str, reqs: typing.List[_WorkerRequest], debug=False) -> typing.List[int]:
    data = [scraper.BookingData(name=req.name, email=req.email, id=req.id)
            for req in reqs]
    if target.startswith(scraper.BASE_URL):
        target = target[len(scraper.BASE_URL):]

    logger.info("Try to book %d appointments for %s", len(reqs), target)

    booked_ids = []
    try:
        svc = scraper.BookingService(target, debug=debug, hide_sensitive_data=True)
        for booked in svc.book(data):
            booked_ids.append(booked.id)
    except:
        logger.exception("Exception while booking")

    logger.info("Booked %d appointments for %s", len(booked_ids), target)

    return booked_ids


class Worker(object):

    def __init__(self, loop: asyncio.AbstractEventLoop, db: model.DatabaseManager, interval=5 * 60, debug=True):
        self._loop = loop
        self._db = db
        self._interval = interval
        self._debug = debug
        self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)

    def _request_queue(self) -> RequestQueue:
        with self._db.make_session_context() as session:
            active_targets = session.query(model.Request.target.label("target"),
                                           func.min(model.Request.created).label("min_c")) \
                .filter(model.Request.resolved == None) \
                .group_by(model.Request.target) \
                .order_by('min_c') \
                .limit(100) \
                .subquery()

            qry = session.query(model.Request) \
                .options(joinedload(model.Request.data)) \
                .join(active_targets, (model.Request.target == active_targets.c.target), ) \
                .filter(model.Request.resolved == None) \
                .filter(model.Request.data != None) \
                .order_by(model.Request.id)

            requests = RequestQueue()
            for req in qry:
                requests.enqueue(req)

            logger.debug("Loaded %d requests for %d targets", requests.request_cnt, requests.target_cnt)
            return requests

    def cleanup_booked(self, booked):
        with self._db.make_session_context() as session:
            qry = session.query(model.Request) \
                .options(joinedload(model.Request.data)) \
                .filter(model.Request.id.in_(booked))

            for item in qry:
                item.resolved = datetime.datetime.utcnow()
                item.state = "success"
                if item.data is not None:
                    session.delete(item.data)

            session.commit()

    def cleanup_old(self):
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=20)
        with self._db.make_session_context() as session:
            qry = session.query(model.Request) \
                .options(joinedload(model.Request.data)) \
                .filter(model.Request.data != None) \
                .filter(model.Request.created < cutoff)

            for item in qry:
                item.resolved = datetime.datetime.utcnow()
                item.state = "timeout"
                if item.data is not None:
                    session.delete(item.data)

            session.commit()

    async def _run_once(self):
        requests = await self._loop.run_in_executor(None, self._request_queue)

        if requests.is_empty():
            return

        booked = []
        for target, reqs in requests.iterate():
            booked.extend(
                await self._loop.run_in_executor(self._executor,
                                                 functools.partial(_book, target, reqs, debug=self._debug)))

        await self._loop.run_in_executor(None, functools.partial(self.cleanup_booked, booked))
        await self._loop.run_in_executor(None, self.cleanup_old)

    async def run(self):
        while True:
            start = datetime.datetime.utcnow()
            await self._run_once()
            end = datetime.datetime.utcnow()
            elapsed = (end - start).total_seconds()
            sleep = max(0.0, self._interval - elapsed)
            logger.debug("Booking run completed in %0.2f seconds - now sleep for %0.2f seconds", elapsed, sleep)
            await asyncio.sleep(sleep)
