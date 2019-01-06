from sqlalchemy import Column, String, Integer, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Target(Base):
    __tablename__ = "target"

    url = Column(String, primary_key=True)
    searches = Column(Integer, default=0)


class Request(Base):
    __tablename__ = "request"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, nullable=False)
    created = Column(DateTime, nullable=False)
    resolved = Column(DateTime, nullable=True)


class RequestData(Base):
    __tablename__ = "request_data"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    accept_terms = Column(Boolean, nullable=False, default=False)
