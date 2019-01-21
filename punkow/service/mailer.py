from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import functools
import logging
import os
import smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import jinja2

from .. import scraper

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class MailConfig(object):
    from_addr: str
    host: str
    port: str
    tls: bool
    user: str = None
    passwd: str = None


def _do_send_email(cfg: MailConfig, to_addr: str, subject: str, text: str):
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = cfg.from_addr
    msg['To'] = to_addr
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid('punkow')

    txt = MIMEText(text)
    msg.attach(txt)

    smtp = smtplib.SMTP(host=cfg.host, port=cfg.port)

    if cfg.tls:
        smtp.starttls()

    if cfg.user is not None:
        smtp.login(cfg.user, cfg.passwd)

    try:
        smtp.sendmail(cfg.from_addr, [to_addr], msg.as_string())
    finally:
        smtp.quit()

    logger.info("Sent an email")


class Mailer(object):
    def __init__(self, loop: asyncio.AbstractEventLoop, config: MailConfig, base_url: str):
        self._loop = loop

        self._config = config
        self._base_url = base_url

        self._tpl = jinja2.Environment(
            loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__),
                                                        'email_templates')),
            autoescape=jinja2.select_autoescape(['html', 'xml'])
        )
        self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=2)

    async def _send_email(self, to_addr, subject, text):
        await self._loop.run_in_executor(self._executor, _do_send_email, self._config, to_addr, subject, text)

    async def send_success_email(self, email, booking: scraper.BookingResult):
        tpl = self._tpl.get_template("success.txt")

        text = tpl.render(meta=booking.metadata, change_url=scraper.BASE_URL + scraper.MANAGE_URL,
                          process_id=booking.process_id, auth_code=booking.auth_key)

        await self._send_email(email, "Your appointment was booked", text)

    async def send_confirmation_email(self, email, req_key):
        tpl = self._tpl.get_template("confirmation.txt")

        text = tpl.render(base_url=self._base_url, req_key=req_key)
        await self._send_email(email, "Your booking request was registered", text)

    async def send_cancel_email(self, email, req_key):
        tpl = self._tpl.get_template("cancel.txt")

        text = tpl.render(base_url=self._base_url, req_key=req_key)
        await self._send_email(email, "Your booking request was canceled", text)

    def start_queue(self) -> AsyncMailQueue:
        return AsyncMailQueue(self._loop, self)


class AsyncMailQueue(object):
    def __init__(self, loop: asyncio.AbstractEventLoop, mailer: Mailer):
        self._loop = loop
        self._mailer = mailer
        self._queue = []

    async def __aenter__(self) -> AsyncMailQueue:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if len(self._queue) != 0:
            await self._loop.run_in_executor(None, functools.partial(
                concurrent.futures.wait, self._queue))

    def _append_task(self, coro):
        self._queue.append(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def send_success_email(self, email, booking: scraper.BookingResult):
        self._append_task(self._mailer.send_success_email(email, booking))

    def send_confirmation_email(self, email, req_key):
        self._append_task(self._mailer.send_confirmation_email(email, req_key))

    async def send_cancel_email(self, email, req_key):
        self._append_task(self._mailer.send_cancel_email(email, req_key))
