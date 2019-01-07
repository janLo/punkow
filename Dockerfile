FROM python:3.7

MAINTAINER "Jan Losinski <losinski@wh2.tu-dresden.de>"

ADD . /service

RUN pip install -r /service/requirements.txt

WORKDIR /service

CMD ./booking_service.py
