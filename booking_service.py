#!/usr/bin/env python

import asyncio
import logging

import uvloop
import click

from punkow.service import interface, model, worker


@click.command()
@click.option("--host", default="127.0.0.1", help="The hostname to bind on")
@click.option("--port", default=8080, type=int, help="The hostname to bind on")
@click.option("--db", default="sqlite:////tmp/punkow.db", help="The database uri")
@click.option("--interval", default=50 * 5, type=int, help="The interval in which the worker should operate")
@click.option("--debug", is_flag=True, help="Run in debug mode")
def main(host, port, db, interval, debug):
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    loop = asyncio.get_event_loop()

    level = logging.INFO
    if debug:
        loop.set_debug(True)
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        #        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    db_mngr = model.DatabaseManager(db)
    db_mngr.create_schema()

    wrk = worker.Worker(loop, db_mngr, interval=interval, debug=debug)
    loop.create_task(wrk.run())

    app = interface.App(db_mngr)
    loop.create_task(app.register_server(host, port))

    loop.run_forever()


if __name__ == "__main__":
    main(auto_envvar_prefix='PUNKOW')
