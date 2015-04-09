__author__ = 'ejc84332'


__author__ = 'ejc84332'
# python
import sqlalchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import sessionmaker

# local
from config import DB_KEY, CON_STRING, TABLE_NAME, KEY_COLOLUMN


class Banner():

    def __init__(self):
        self.engine, self.metadata, self.session = self.get_connection()
        # Am I goign to get into trouble for re-using these curors?
        self.call_cursor, self.result_cursor = self.get_cursor_connection()

    def get_cursor_connection(self):
        conn = self.engine.raw_connection()
        # Get a cursor to call the procedure and a cursor to store the results.
        call_cursor = conn.cursor()
        result_cursor = conn.cursor()
        return call_cursor, result_cursor

    def get_connection(self):
        # Login

        constr = CON_STRING % DB_KEY

        engine = sqlalchemy.create_engine(constr, echo=True)

        metadata = MetaData(engine)
        session = sessionmaker(bind=engine)()

        return engine, metadata, session

    def execute(self, sql):
        result = self.session.execute(sql)
        self.session.commit()
        return result

    def get_program_data(self, code=None):

        sql = "select unique * from %s" % TABLE_NAME

        if code:
            sql += " where %s = '%s'" % (KEY_COLOLUMN, code)

        return self.execute(sql)
