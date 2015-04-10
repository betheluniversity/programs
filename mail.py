__author__ = 'ejc84332'

from flask.ext.mail import Mail, Message


def send_message(subject, body):

    from sync import app


    mail = Mail(app)


    msg = Message(subject=subject, body=body,
                  sender="no-reply@bethel.edu",
                  recipients=["e-jameson@bethel.edu"])

    mail.send(msg)