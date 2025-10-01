from sqlalchemy import create_engine
from models import Base  # Import Base from models.py

engine = create_engine('sqlite:///nutricare.db')
Base.metadata.create_all(engine)