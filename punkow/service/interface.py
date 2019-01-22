from __future__ import annotations

import asyncio
import datetime
import functools
import logging
import os
import typing
import uuid

import aiohttp_jinja2
import jinja2

from aiohttp import web
from email_validator import validate_email, EmailNotValidError
from sqlalchemy.orm import joinedload

from . import model, mailer

logger = logging.Logger(__name__)


class Validator(object):
    EMAIL_FIELD = "email"
    NAME_FIELD = "name"
    URL_FIELD = "url"
    ACCEPT_FIELD = "terms_accepted"

    def __init__(self, data: typing.Dict[str, str]):
        self.data = data
        self.errors = {}

        self.url = None  # type: str
        self.email = None  # type: str
        self.name = None  # type: str
        self.terms_accepted = False

    async def _validate_email(self, address):
        val = await asyncio.get_event_loop().run_in_executor(None, validate_email, address)
        return val[Validator.EMAIL_FIELD]

    async def validate_emails(self):
        errors = {}
        if Validator.EMAIL_FIELD not in self.data or 0 == len(self.data[Validator.EMAIL_FIELD]):
            errors[Validator.EMAIL_FIELD] = "Email address not given or empty"
        else:
            try:
                email = await self._validate_email(self.data[Validator.EMAIL_FIELD])
                self.email = email
            except EmailNotValidError:
                errors[Validator.EMAIL_FIELD] = "Email is not a valid email address"

        self.errors.update(errors)
        return 0 == len(errors)

    async def validate_name(self):
        errors = {}
        if Validator.NAME_FIELD not in self.data or 0 == len(self.data[Validator.NAME_FIELD]):
            errors[Validator.NAME_FIELD] = "Name not given or empty"
        else:
            if len(self.data[Validator.NAME_FIELD]) < 3:
                errors[Validator.NAME_FIELD] = "Name too short"
            else:
                self.name = self.data[Validator.NAME_FIELD].strip()

        self.errors.update(errors)
        return 0 == len(errors)

    async def validate_url(self):
        errors = {}
        if Validator.URL_FIELD not in self.data or 0 == len(self.data[Validator.URL_FIELD]):
            errors[Validator.URL_FIELD] = "Start url not given or empty"
        else:
            if not (self.data[Validator.URL_FIELD].startswith("/terminvereinbarung/termin/tag.php?") or
                    self.data[Validator.URL_FIELD].startswith(
                        "https://service.berlin.de/terminvereinbarung/termin/tag.php?")):
                errors[Validator.URL_FIELD] = "Start url invalid!"
            else:
                self.url = self.data[Validator.URL_FIELD].strip()

        self.errors.update(errors)
        return 0 == len(errors)

    async def validate_terms(self):
        errors = {}
        if Validator.ACCEPT_FIELD not in self.data or self.data[Validator.ACCEPT_FIELD] != "accepted":
            errors[Validator.ACCEPT_FIELD] = "Terms not accepted!"
        else:
            self.terms_accepted = True

        self.errors.update(errors)
        return 0 == len(errors)


class _ViewBase(web.View):
    def __init__(self, *args, **kwargs):
        super(_ViewBase, self).__init__(*args, **kwargs)

    @property
    def _mail(self) -> mailer.Mailer:
        return self.request.app['mailer']

    @property
    def _db(self) -> model.DatabaseManager:
        return self.request.app['db']  # type: model.DatabaseManager


class CreateEntryView(_ViewBase):
    @aiohttp_jinja2.template("form.html")
    def get(self):
        return {'errors': {}, 'data': {}}

    @aiohttp_jinja2.template("form.html")
    async def post(self):
        data = await self.request.post()
        errors = {}

        validator = Validator(data)

        if all(await asyncio.gather(validator.validate_emails(),
                                    validator.validate_name(),
                                    validator.validate_url(),
                                    validator.validate_terms())):
            with self._db.make_session_context() as session:
                if not validator.terms_accepted:
                    errors["terms"] = "Terms not accepted?"
                elif session \
                        .query(model.Request) \
                        .join(model.Request.data) \
                        .filter(model.Request.target == validator.url) \
                        .filter(model.RequestData.name == validator.name) \
                        .filter(model.RequestData.email == validator.email) \
                        .count() > 0:
                    errors["duplicate"] = "Booking with this data already exist!"
                else:
                    key = str(uuid.uuid4())

                    request = model.Request(target=validator.url, key=key, created=datetime.datetime.utcnow())
                    request_data = model.RequestData(name=validator.name, email=validator.email,
                                                     accept_terms=validator.terms_accepted)
                    request_data.request = request
                    session.add_all([request, request_data])

                    session.commit()

                    loop = asyncio.get_event_loop()

                    def send_mail(mail, email, key):
                        loop.create_task(mail.send_confirmation_email(email, key))

                    loop.call_soon_threadsafe(
                        functools.partial(send_mail, self._mail, request_data.email, request.key))

                    raise web.HTTPFound(self.request.app.router["detail"].url_for(entry_id=key))

        errors.update(validator.errors)

        return {'errors': errors, 'data': data}


def with_entry(fn):
    @functools.wraps(fn)
    async def wrapper(*args):
        self = args[0]

        errors = {}
        data = None
        if 'entry_id' not in self.request.match_info:
            errors["none"] = "No entry key given!"

        else:
            key = self.request.match_info["entry_id"]

            with self._db.make_session_context() as session:
                qry = session.query(model.Request) \
                    .filter(model.Request.key == key)
                data = qry.first()

                if data is None:
                    errors["notfound"] = "No entry found for the given key"

        return await fn(*args, entry=data, errors=errors)

    return wrapper


class EntryDetailView(_ViewBase):
    @aiohttp_jinja2.template("detail.html")
    @with_entry
    async def get(self, entry: model.Request, errors):
        del_url = "#"
        if entry is not None:
            del_url = self.request.app.router["cancel"].url_for(entry_id=entry.key)

        return {"errors": errors, "data": entry, "del_url": del_url}


class DeleteEntryView(_ViewBase):
    @aiohttp_jinja2.template("delete.html")
    @with_entry
    async def get(self, entry: model.Request, errors):
        del_url = "#"
        if entry is not None:
            del_url = self.request.app.router["cancel"].url_for(entry_id=entry.key)

        return {"data": entry, "errors": errors, "del_url": del_url}

    @aiohttp_jinja2.template("detail.html")
    @with_entry
    async def post(self, entry: model.Request, errors):
        if entry is not None:
            with self._db.make_session_context() as session:
                qry = session.query(model.Request) \
                    .options(joinedload(model.Request.data)) \
                    .filter(model.Request.key == entry.key)

                del_entry = qry.first()  # type: model.Request
                if del_entry.data is not None:
                    session.delete(del_entry.data)
                    del_entry.resolved = datetime.datetime.utcnow()
                    del_entry.state = "cancelled"

                    session.commit()
                    raise web.HTTPFound(self.request.app.router["detail"].url_for(entry_id=entry.key))

                else:
                    errors["already"] = "Already cancelled!"

        return {"data": entry, "errors": errors}


def simple_view(template):
    async def nop(_):
        pass

    return aiohttp_jinja2.template(template)(nop)


class App(object):
    def __init__(self, database_manager: model.DatabaseManager, mail: mailer.Mailer, base_url: str):
        self.db = database_manager
        self.mail = mail
        self.base_url = base_url
        self.app = web.Application()
        self.site = None  # type: web.TCPSite

        self.setup_app()
        self.setup_routes()

    def setup_app(self):
        self.app['db'] = self.db
        self.app["mailer"] = self.mail
        aiohttp_jinja2.setup(
            self.app, loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates')))

        self.app['static_root_url'] = '/static'
        self.app['cfg_base_url'] = self.base_url

    def setup_routes(self):
        self.app.router.add_static('/static/',
                                   path=os.path.join(os.path.dirname(__file__), 'static'),
                                   name='static')
        self.app.router.add_view("/", simple_view("index.html"), name="index")
        self.app.router.add_view("/create", CreateEntryView, name="create")
        self.app.router.add_view("/show/{entry_id}", EntryDetailView, name="detail")
        self.app.router.add_view("/cancel/{entry_id}", DeleteEntryView, name="cancel")
        self.app.router.add_get("/terms", simple_view("terms.html"))

    def run(self, host: str = None, port: int = None):
        web.run_app(self.app, host=host, port=port)

    async def start(self, host: str = None, port: int = None):
        app_runner = web.AppRunner(self.app, access_log=logger)
        await app_runner.setup()
        self.site = web.TCPSite(app_runner, host, port)
        await self.site.start()

    async def stop(self):
        if self.site is not None:
            await self.site.stop()
