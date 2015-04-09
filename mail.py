__author__ = 'ejc84332'

from mailer import Mailer
from mailer import Message


def send_message(subject, body):

    message = Message(From="wufoo@bethel.edu",
                      To="e-jameson@bethel.edu")
    message.Subject = subject
    message.Html = body

    sender = Mailer('localhost')
    sender.send(message)