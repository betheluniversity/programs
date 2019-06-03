import socket
from flask_mail import Mail, Message
from config import ADMIN_RECIPIENTS, CAPS_GS_SEM_RECIPIENTS


def send_message(subject, body, html=False, caps_gs_sem=False):
    from sync import app

    mail = Mail(app)

    if caps_gs_sem:
        recipients = CAPS_GS_SEM_RECIPIENTS
    else:
        recipients = ADMIN_RECIPIENTS
    msg = Message(subject=subject,
                  sender='web-development@bethel.edu',
                  recipients=recipients)

    if html:
        msg.html = body
    else:
        msg.body = body

    try:
        mail.send(msg)
    except socket.error:
        print('failed to send message %s' % body)
        return False

    return True
