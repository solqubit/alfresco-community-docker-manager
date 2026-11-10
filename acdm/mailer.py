"""Test message sent by the manager with Alfresco's SMTP settings before applying them.

Alfresco itself sends invitations, notifications and task emails with these settings.
"""
from __future__ import annotations

from email.message import EmailMessage
import smtplib
import socket
import ssl

from .core import Error

TIMEOUT = 30


def send(mail, recipient, subject, body):
    """Send one plain-text message. The password never appears in errors or logs."""
    message = EmailMessage()
    message['From'] = mail['sender']
    message['To'] = recipient
    message['Subject'] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    try:
        if mail['security'] == 'ssl':
            client = smtplib.SMTP_SSL(mail['host'], int(mail['port']), timeout=TIMEOUT, context=context)
        else:
            client = smtplib.SMTP(mail['host'], int(mail['port']), timeout=TIMEOUT)
        with client:
            client.ehlo()
            if mail['security'] == 'starttls':
                client.starttls(context=context)
                client.ehlo()
            if mail['username']:
                client.login(mail['username'], mail['password'])
            client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise Error('El servidor de correo rechazó el usuario o la contraseña / The mail server rejected the username or password') from exc
    except smtplib.SMTPNotSupportedError as exc:
        raise Error('El servidor de correo no admite ese cifrado o autenticación; prueba otro tipo de seguridad o puerto / '
                    'The mail server does not support that encryption or authentication; try another security type or port') from exc
    except ssl.SSLError as exc:
        raise Error('Error de cifrado con el servidor de correo; revisa el puerto y la seguridad (587 STARTTLS, 465 SSL) / '
                    'Encryption error with the mail server; check port and security (587 STARTTLS, 465 SSL)') from exc
    except (socket.timeout, TimeoutError) as exc:
        raise Error('El servidor de correo no respondió; revisa el nombre, el puerto o el cortafuegos (muchos proveedores bloquean el 25) / '
                    'The mail server did not answer; check name, port or firewall (many providers block port 25)') from exc
    except (smtplib.SMTPException, OSError) as exc:
        detail = str(exc).replace(mail.get('password') or '\0', '[hidden]')[:300]
        raise Error(f'No se pudo enviar el correo: {detail} / Could not send the email: {detail}') from exc


def test(mail, recipient, url):
    send(mail, recipient, 'Alfresco: correo de prueba / test email',
         'El correo saliente de Alfresco está configurado correctamente.\n'
         'Alfresco outbound email is configured correctly.\n\n' + url + '\n')
