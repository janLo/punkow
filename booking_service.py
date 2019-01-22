#!/usr/bin/env python

import asyncio
import logging
import signal

import uvloop
import click

from punkow.service import interface, model, worker, timer, mailer


@click.command()
@click.option("--host", default="127.0.0.1", help="The hostname to bind on")
@click.option("--port", default=8080, type=int, help="The hostname to bind on")
@click.option("--db", default="sqlite:////tmp/punkow.db", help="The database uri")
@click.option("--interval", default=50 * 5, type=int, help="The interval in which the worker should operate")
@click.option("--debug", is_flag=True, help="Run in debug mode")
@click.option("--mail-from", required=True, help="The Email Address to send the mails from")
@click.option("--mail-host", required=True, help="The Email SMTP Host")
@click.option("--mail-port", default=587, type=int, help="The Email SMTP Port")
@click.option("--mail-tls", is_flag=True, help="Use TLS for SMTP")
@click.option("--mail-user", default=None, help="The Email SMTP password")
@click.option("--mail-passwd", default=None, help="The Email SMTP username")
@click.option("--domain", required=True, help="The domain this service is running on")
@click.option("--tz", default="CET", help="Timezone to use for special times")
@click.option("--special", help="special time where the interval should be increased", multiple=True)
def main(host, port, db, interval, debug, tz, special,
         mail_from, mail_host, mail_port, mail_tls, mail_user, mail_passwd, domain):
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    loop = asyncio.get_event_loop()

    level = logging.INFO
    if debug:
        loop.set_debug(True)
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    logger = logging.getLogger("booking_service")

    db_mngr = model.DatabaseManager(db)
    db_mngr.create_schema()

    url = f"https://{domain}"
    mail_cfg = mailer.MailConfig(from_addr=mail_from, host=mail_host, port=mail_port, tls=mail_tls,
                                 user=mail_user, passwd=mail_passwd)
    mail = mailer.Mailer(loop=loop, config=mail_cfg, base_url=url)

    tm = timer.Timer(interval=interval, special_times=special, time_zone=tz)

    wrk = worker.Worker(loop, db_mngr, tm=tm, mail=mail, debug=debug)
    wrk.start()

    app = interface.App(db_mngr, mail, base_url=url)
    loop.create_task(app.start(host, port))

    def stop():
        logger.info("Stop Punkow ... ")

        async def _do_stop():
            await asyncio.gather(wrk.stop(), app.stop())
            loop.stop()
            logger.info("Goodbye!")

        loop.create_task(_do_stop())

    loop.add_signal_handler(signal.SIGINT, stop)
    loop.add_signal_handler(signal.SIGTERM, stop)

    loop.run_forever()


if __name__ == "__main__":
    main(auto_envvar_prefix='PUNKOW')
