import contextlib
import logging
import typing

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = 'https://service.berlin.de'
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36'
BLANK_URL = '/terminvereinbarung/termin/blank.png'
REGISTER_URL = "/terminvereinbarung/termin/register/"
ABORT_URL = '/terminvereinbarung/termin/abort/'
MANAGE_URL = '/terminvereinbarung/termin/manage/'


def print_url(r, *args, **kwargs):
    logger.debug("loaded: %s - status: %d", r.url, r.status_code)
    logger.debug("request headers:  %s", r.request.headers)
    logger.debug("response headers: %s", r.headers)


class BookingData(typing.NamedTuple):
    name: str
    email: str
    id: typing.Optional[int]


class BookingService(object):
    def __init__(self, start_url, debug=True, hide_sensitive_data=False):
        self.session = None
        self.start_url = start_url
        self.debug = debug
        self.sensitive = hide_sensitive_data

        self._init_session()

    def _init_session(self):
        if self.session is not None:
            self.session.close()

        self.session = requests.session()
        self.session.headers.update({'User-Agent': USER_AGENT, })
        if self.debug:
            self.session.hooks.update({'response': print_url})

    @contextlib.contextmanager
    def _local_referrer(self):
        old_referrer = self.session.headers.get('Referrer', None)
        yield
        if old_referrer is not None:
            self.session.headers.update({'Referrer': old_referrer})

    def _print_details(self, zms, depth):
        if self.sensitive:
            depth = min(depth, 3)
        meta = {}
        groups = zms.find_all("div", "collapsible-toggle")
        for group in groups[:depth]:
            title = group.find("div", {"class": "collapsible-title"})
            desc = group.find("div", {"class": "collapsible-description"})

            if title is None or title == -1:
                break
            if desc is None or desc == -1:
                break
            if desc.text.strip() == "":
                break

            title = title.text.strip()
            desc = next(desc.stripped_strings)

            logging.debug("  %s: %s", title, desc)
            meta[title] = desc

        return meta

    def _fetch_soup(self, url):
        response = self.session.get(BASE_URL + url)

        assert response.status_code == 200, f"Could not get a proper response for {url}"

        self.session.headers.update({'Referrer': response.url})
        self.session.get(BASE_URL + BLANK_URL)

        return BeautifulSoup(response.content, 'html.parser')

    def _iter_bookable_day_urls(self, start_url):
        html = self._fetch_soup(start_url)

        zms = html.find("div", {"class": "zms"})
        self._print_details(zms, 2)

        checked = []

        while html is not None:
            months = html.find_all("div", {"class": "calendar-month-table"})
            for m_div in months:
                month_name = m_div.find("th", {"class": "month"}).text.strip()
                if month_name in checked:
                    logging.debug("Month %s aleady checked - skipping", month_name)
                    continue

                bookable = m_div.find_all('td', {"class": 'buchbar'})
                logger.debug("Found month %s with %d available days", month_name, len(bookable))

                for day in bookable:
                    day_link = day.find("a")

                    if day_link and day_link != -1:
                        logger.debug("Search free slots for day %s. %s", day_link.text.strip(), month_name)
                        yield day_link.attrs["href"]

                checked.append(month_name)

            next_field = html.find("th", {"class": "next"})
            if next_field is None or next_field == -1:
                break
            next_link = next_field.find("a")
            if next_link is None or next_link == -1:
                break

            html = self._fetch_soup(next_link["href"])

        logger.debug("No more days with appointments")

    def _iter_bookable_times(self, day_url):
        html = self._fetch_soup(day_url)

        timetable = html.find("div", {"class": "timetable"})
        if not timetable or timetable == -1:
            logger.warning("No timetable found in %s", day_url)
            return

        for row in timetable.find_all("tr"):
            head = row.find("th", {"class": "buchbar"})

            if head and head != 1:
                logger.debug("Found timeslot %s", head.text.strip())

                content = row.find("td", {"class": "frei"})
                if not content or content == -1:
                    logger.warning("No link field for this slot")
                    continue

                link = content.find("a")
                if not link or link == -1:
                    logger.warning("No Link tag for this slot")
                    continue

                yield link.attrs["href"]

        logger.debug("No more free slots for the day.")

    def _book_appointment(self, slot_url, name=None, email=None):
        html = self._fetch_soup(slot_url)

        zms = html.find("div", {"class": "zms"})

        logger.debug("Loaded booking form:")
        self._print_details(zms, 4)

        formdata = {"familyName": name, "email": email, "form_validate": "1", "agbgelesen": "1"}
        process = zms.find("input", {"id": "process"})
        if process is None or process == -1:
            logger.error("No process id found")
            self.session.get(BASE_URL + ABORT_URL)
            return False

        formdata["process"] = process.attrs["value"]

        if not self.sensitive:
            logger.info("Book with: %s", formdata)

        if self.debug:
            logger.warning("Not really booked as we're in debug mode!")
            self.session.get(BASE_URL + ABORT_URL)
            logger.warning("Aborted!")
            return True

        response = self.session.post(BASE_URL + REGISTER_URL, data=formdata)
        if response.status_code != 200:
            logger.error("Could not book appointment. Status: %d", response.status_code)
            return False

        register_html = BeautifulSoup(response.content, 'html.parser')
        success = register_html.find("div", {"class": "submit-success-message"})

        if success is None or success == -1:
            logger.error("Cannot find success message")
            return False

        logger.debug("Success: %s", success.text.strip())

        result = {key: register_html.find("span", {"class": f"summary_{key}"}).text.strip()
                  for key in ("name", "mail", "authKey", "processId")}
        if self.sensitive:
            logger.info("Registration for %s with number %s", self.start_url, result["processId"])
        else:
            logger.info("  Registratred for: %s (%s)", result["name"], result["mail"])
            logger.info("  To change or cancel use %s with the number %s and the code %s", BASE_URL + MANAGE_URL,
                        result["processId"], result["authKey"])

        return True

    def book(self, data: typing.List[BookingData]):
        logging.info("Look for appointments at %s", BASE_URL + self.start_url)

        data_iter = iter(data)
        cur_data = next(data_iter, None)
        if cur_data is None:
            return

        for day_url in self._iter_bookable_day_urls(self.start_url):
            with self._local_referrer():
                for slot_url in self._iter_bookable_times(day_url):
                    if self._book_appointment(slot_url, cur_data.name, cur_data.email):
                        yield cur_data
                        cur_data = next(data_iter, None)
                        if cur_data is None:
                            return
        return
