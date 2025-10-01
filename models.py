from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True)
    caregiver_name = Column(String)
    children = relationship("Child", back_populates="user")

class Child(Base):
    __tablename__ = 'children'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    child_name = Column(String)
    age_months = Column(Integer)
    sex = Column(String)  # 'male' or 'female'
    user = relationship("User", back_populates="children")
    measurements = relationship("Measurement", back_populates="child")

class Measurement(Base):
    __tablename__ = 'measurements'
    id = Column(Integer, primary_key=True)
    child_id = Column(Integer, ForeignKey('children.id'))
    date = Column(DateTime, default=datetime.utcnow)
    weight = Column(Float)
    height = Column(Float)
    muac = Column(Float, nullable=True)
    bmi_z = Column(Float)
    status = Column(String)  # 'SAM', 'MAM', 'NORMAL'
    child = relationship("Child", back_populates="measurements") 
