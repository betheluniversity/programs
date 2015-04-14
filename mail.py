__author__ = 'ejc84332'

import socket

from flask.ext.mail import Mail, Message

from config import RECIPIENTS

def send_message(subject, body):

    from sync import app


    mail = Mail(app)


    msg = Message(subject=subject, body=body,
                  sender="programs-sync@bethel.edu",
                  recipients=RECIPIENTS)

    try:
        mail.send(msg)
    except socket.error:
        print "failed to send message %s" % body
        return False

    return True
