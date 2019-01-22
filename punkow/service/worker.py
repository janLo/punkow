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


class RequestBatchQueue(object):
    def __init__(self):
        self._targets = []  # type: typing.List[str]
        self._requests = {}  # type: typing.Dict[str, typing.List[_WorkerRequest]]

    def enqueue(self, req: model.Request):
        if req.target not in self._requests:
            self._targets.append(req.target)
            self._requests[req.target] = []

        self._requests[req.target].append(
            _WorkerRequest(req.id, req.data.name, req.data.email, req.target))

    def batches(self) -> typing.Generator[typing.List[_WorkerRequest], typing.List[_WorkerRequest], None]:
        iterators = {key: iter(val) for key, val in self._requests.items()}

        exclude = set()

        while True:
            cur_batch = [req for req
                         in (next(iterators[target], None)
                             for target
                             in self._targets
                             if target not in exclude)
                         if req is not None]
            if len(cur_batch) == 0:
                break

            failed = yield cur_batch
            if failed is not None:
                exclude.update(req.target for req in failed)

    def is_empty(self):
        return len(self._targets) == 0

    @property
    def target_cnt(self):
        return len(self._targets)

    @property
    def request_cnt(self):
        return sum(len(queue) for queue in self._requests.values())


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
        self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=2)

    def _request_queue(self) -> RequestBatchQueue:
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

            requests = RequestBatchQueue()
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

    async def _process_request(self, request, mail):
        booking = await self._loop.run_in_executor(self._executor,
                                                   functools.partial(_book, request, debug=self._debug))
        if booking is not None:
            mail.send_success_email(request.email, booking)
            return request.id

        return None

    async def _run_once(self):
        requests = await self._loop.run_in_executor(None, self._request_queue)

        if requests.is_empty():
            return

        booked_ids = []
        async with self._mailer.start_queue() as mail:
            request_batch_generator = requests.batches()
            request_batch = next(request_batch_generator, None)

            while request_batch is not None and len(request_batch) > 0:

                booked_result = await asyncio.gather(*[self._process_request(req, mail)
                                                       for req in request_batch])

                booked_id_set = set([rid for rid in booked_result if rid is not None])
                booked_ids.extend(booked_id_set)
                try:
                    request_batch = request_batch_generator.send([req for req
                                                                  in request_batch
                                                                  if req.id not in booked_id_set])
                except StopIteration:
                    break

            await self._loop.run_in_executor(None, functools.partial(self.cleanup_booked, booked_ids))
            await self._loop.run_in_executor(None, functools.partial(self.cleanup_old, mail))

    async def run(self):
        while True:
            async with self._timer.timed():
                await self._run_once()
