import asyncio
import concurrent.futures
import datetime
import functools
import logging
import typing

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from . import model, timer, mailer
from .. import scraper

logger = logging.getLogger(__name__)


class _WorkerRequest(typing.NamedTuple):
    id: int
    name: str
    email: str
    target: str


class RequestQueue(object):
    def __init__(self):
        self._targets = []  # type: typing.List[str]
        self._requests = []  # type: typing.List[_WorkerRequest]

    def enqueue(self, req: model.Request):
        if req.target not in self._requests:
            self._targets.append(req.target)

        self._requests.append(
            _WorkerRequest(req.id, req.data.name, req.data.email, req.target))

    def iterate(self) -> typing.Generator[_WorkerRequest, None, None]:
        for item in self._requests:
            yield item

    def is_empty(self):
        return len(self._targets) == 0

    @property
    def target_cnt(self):
        return len(self._targets)

    @property
    def request_cnt(self):
        return len(self._requests)


def _book(req: _WorkerRequest, debug=False) -> typing.Optional[scraper.BookingResult]:
    data = scraper.BookingData(name=req.name, email=req.email)
    target = req.target
    if req.target.startswith(scraper.BASE_URL):
        target = req.target[len(scraper.BASE_URL):]

    logger.debug("Try to book one appointment for %s", target)

    try:
        svc = scraper.BookingService(target, debug=debug, hide_sensitive_data=True)
        booked = svc.book(data)
        if booked is not None:
            logger.info("Booked an appointments for %s", target)
            return booked
    except:
        logger.exception("Exception while booking")

    return None


class Worker(object):

    def __init__(self, loop: asyncio.AbstractEventLoop, db: model.DatabaseManager,
                 tm: timer.Timer, mail: mailer.Mailer, debug=True):
        self._loop = loop
        self._db = db
        self._timer = tm
        self._mailer = mail
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

    def cleanup_old(self, mail_queue: mailer.AsyncMailQueue):
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
                    mail_queue.send_cancel_email(item.data.email, item.key)
                    session.delete(item.data)

            session.commit()

    async def _run_once(self):
        requests = await self._loop.run_in_executor(None, self._request_queue)

        if requests.is_empty():
            return

        booked_ids = []
        async with self._mailer.start_queue() as mail:
            for req in requests.iterate():
                booking = await self._loop.run_in_executor(self._executor,
                                                           functools.partial(_book, req, debug=self._debug))
                if booking is not None:
                    booked_ids.append(req.id)
                    mail.send_success_email(req.email, booking)

            await self._loop.run_in_executor(None, functools.partial(self.cleanup_booked, booked_ids))
            await self._loop.run_in_executor(None, functools.partial(self.cleanup_old, mail))

    async def run(self):
        while True:
            async with self._timer.timed():
                await self._run_once()
