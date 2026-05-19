#app/database/models.py

from sqlalchemy import Column, Integer, String, DateTime, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()            # ← era "base" (minúscula)

class Message(Base):
    __tablename__ = "messages"       # ← pluralizado por convenção

    id         = Column(Integer, primary_key=True, index=True)
    sender     = Column(String, nullable=False)
    message    = Column(String, nullable=False)
    response   = Column(String)      # ← novo: salva a resposta gerada
    created_at = Column(DateTime, server_default=func.now())  # ← novo: timestamp

