from app.database.connection import engine
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execute(text("SELECT 1")).scalar()
    print(result)