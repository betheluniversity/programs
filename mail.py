__author__ = 'ejc84332'

import socket

from flask.ext.mail import Mail, Message

from config import RECIPIENTS, CAPS_GS_RECIPIENTS, BCC

def send_message(subject, body, html=False, caps_gs=False):

    from sync import app

    mail = Mail(app)

    if caps_gs:
        recipients = CAPS_GS_RECIPIENTS
        bcc = BCC
    else:
        recipients = RECIPIENTS
        bcc = None
    msg = Message(subject=subject,
                  sender="no-reply@bethel.edu",
                  recipients=recipients,
                  bcc=bcc)

    if html:
        msg.html = body
    else:
        msg.body = body

    try:
        mail.send(msg)
    except socket.error:
        print "failed to send message %s" % body
        return False

    return True
