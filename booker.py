import argparse
import logging
import datetime
import time

from punkow.scraper import BookingService, BASE_URL

logger = logging.getLogger(__name__)

START_URL = '/terminvereinbarung/termin/tag.php?termin=1&dienstleister=122301&anliegen[]=120686&herkunft=1'  # anmeldung wohnung pankow
# START_URL = '/terminvereinbarung/termin/tag.php?termin=1&dienstleister=327427&anliegen[]=318998&herkunft=1' # einbürgerung pankow


parser = argparse.ArgumentParser(description="Try to book an appointment at berlin cvil services")
parser.add_argument("--name", required=True, help="The name of the applicant")
parser.add_argument("--email", required=True, help="The email of the applicant")
parser.add_argument("--url", default=START_URL, help="The start orl of the booking process")
parser.add_argument("--interval", default=30, type=int, help="The interval the booking is tried")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    args = parser.parse_args()

    url = args.url
    if url.startswith(BASE_URL):
        url = url[len(BASE_URL):]
        logger.info("Use url %s", url)

    while True:
        try:
            logger.info("Try to get an appointment at %s", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            svc = BookingService(url, debug=False)
            if svc.book(name=args.name, email=args.email):
                break
            time.sleep(max(30, args.interval))
        except KeyboardInterrupt:
            logger.info("Got keyboard interrupt - stopping.")
            break
        except:
            logger.exception("Got an exception while booking an appointment")
