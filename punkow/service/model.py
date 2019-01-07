import sqlalchemy
from sqlalchemy import Column, String, Integer, DateTime, Boolean, Enum, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


class Request(Base):
    __tablename__ = "request"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, nullable=False, index=True)
    target = Column(String, nullable=False, index=True)
    created = Column(DateTime, nullable=False)
    resolved = Column(DateTime, nullable=True)
    state = Column(Enum("queued", "processing", "success", "cancelled", "timeout", "error"), default="queued")

    data = relationship("RequestData", uselist=False, back_populates="request")


class RequestData(Base):
    __tablename__ = "request_data"

    id = Column(ForeignKey(Request.id), primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    accept_terms = Column(Boolean, nullable=False, default=False)

    request = relationship("Request", uselist=False, back_populates="data")


class SessionContextManager(object):
    def __init__(self, session: sqlalchemy.orm.Session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._session.close()


class DatabaseManager(object):
    def __init__(self, db_uri):
        self._engine = create_engine(db_uri)
        self._session_maker = sessionmaker(autocommit=False,
                                           autoflush=False,
                                           bind=self._engine)

    def make_session_context(self):
        return SessionContextManager(self._session_maker())

    def create_schema(self):
        Base.metadata.create_all(self._engine)
