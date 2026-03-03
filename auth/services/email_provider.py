"""
Email provider abstraction + implementations.

Security rules:
- Never log raw email addresses, tokens, or OTP codes.
- Console provider masks PII; only dev mode prints OTP.
- SMTP/Gmail errors are logged without credential details.
"""

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape as html_escape

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """r***s@gmail.com"""
    try:
        local, domain = email.split('@', 1)
        if len(local) <= 2:
            return local[0] + '***@' + domain
        return local[0] + '***' + local[-1] + '@' + domain
    except (ValueError, IndexError):
        return '***'


class EmailProvider(ABC):
    @abstractmethod
    def send_password_reset(self, to_email: str, reset_link: str, user_name: str) -> bool:
        ...

    @abstractmethod
    def send_otp(self, to_email: str, otp_code: str, user_name: str) -> bool:
        ...


# ── Console provider (dev mode) ─────────────────────────────────────────────

class ConsoleEmailProvider(EmailProvider):
    """Prints to stdout for local development. Masks PII in non-debug mode."""

    def send_password_reset(self, to_email: str, reset_link: str, user_name: str) -> bool:
        masked = _mask_email(to_email)
        print('\n' + '=' * 60)
        print('  PASSWORD RESET EMAIL (dev mode)')
        print('=' * 60)
        print(f'  To:   {masked}')
        print(f'  Link: [reset link generated]')
        print('=' * 60 + '\n')
        return True

    def send_otp(self, to_email: str, otp_code: str, user_name: str) -> bool:
        masked = _mask_email(to_email)
        # In console/dev mode, print the OTP so developer can test locally.
        # This provider should NEVER be used in production.
        flask_env = os.environ.get('FLASK_ENV', 'development')
        if flask_env == 'production':
            logger.error('ConsoleEmailProvider must not be used in production')
            return False
        print('\n' + '=' * 60)
        print('  EMAIL VERIFICATION OTP (dev mode)')
        print('=' * 60)
        print(f'  To:   {masked}')
        print(f'  OTP:  {otp_code}')
        print('=' * 60 + '\n')
        return True


# ── SMTP provider ────────────────────────────────────────────────────────────

class SmtpEmailProvider(EmailProvider):
    def _get_config(self):
        """Read SMTP config lazily on every send."""
        return {
            'host': os.environ.get('SMTP_HOST', 'smtp.gmail.com'),
            'port': int(os.environ.get('SMTP_PORT', '587')),
            'user': os.environ.get('SMTP_USER', ''),
            'password': os.environ.get('SMTP_PASS', ''),
            'sender': os.environ.get('SMTP_SENDER', os.environ.get('SMTP_USER', '')),
        }

    def _send(self, to_email: str, subject: str, html: str) -> bool:
        cfg = self._get_config()
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = cfg['sender']
        msg['To'] = to_email
        msg.attach(MIMEText(html, 'html'))

        try:
            with smtplib.SMTP(cfg['host'], cfg['port']) as server:
                server.starttls()
                server.login(cfg['user'], cfg['password'])
                server.sendmail(cfg['sender'], to_email, msg.as_string())
            return True
        except Exception:
            logger.exception('SMTP send failed to %s', _mask_email(to_email))
            return False

    def send_password_reset(self, to_email: str, reset_link: str, user_name: str) -> bool:
        return self._send(to_email, 'Reset your GenBI password',
                          _reset_email_html(user_name, reset_link))

    def send_otp(self, to_email: str, otp_code: str, user_name: str) -> bool:
        return self._send(to_email, 'Your GenBI verification code',
                          _otp_email_html(user_name, otp_code))


# ── Gmail API provider ───────────────────────────────────────────────────────

class GmailApiEmailProvider(EmailProvider):
    def _get_config(self):
        """Read Gmail config lazily on every send."""
        return {
            'client_id': os.environ.get('GMAIL_CLIENT_ID', ''),
            'client_secret': os.environ.get('GMAIL_CLIENT_SECRET', ''),
            'refresh_token': os.environ.get('GMAIL_REFRESH_TOKEN', ''),
            'sender': os.environ.get('GMAIL_SENDER', ''),
        }

    def _send(self, to_email: str, subject: str, html: str) -> bool:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            import base64

            cfg = self._get_config()
            creds = Credentials(
                token=None,
                refresh_token=cfg['refresh_token'],
                client_id=cfg['client_id'],
                client_secret=cfg['client_secret'],
                token_uri='https://oauth2.googleapis.com/token',
            )
            service = build('gmail', 'v1', credentials=creds)

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = cfg['sender']
            msg['To'] = to_email
            msg.attach(MIMEText(html, 'html'))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId='me', body={'raw': raw}).execute()
            return True
        except Exception:
            logger.exception('Gmail API send failed to %s', _mask_email(to_email))
            return False

    def send_password_reset(self, to_email: str, reset_link: str, user_name: str) -> bool:
        return self._send(to_email, 'Reset your GenBI password',
                          _reset_email_html(user_name, reset_link))

    def send_otp(self, to_email: str, otp_code: str, user_name: str) -> bool:
        return self._send(to_email, 'Your GenBI verification code',
                          _otp_email_html(user_name, otp_code))


# ── Azure Communication Services provider ────────────────────────────────────

class AcsEmailProvider(EmailProvider):
    """Send email via Azure Communication Services."""

    def _send(self, to_email: str, subject: str, html: str) -> bool:
        try:
            from azure.communication.email import EmailClient

            conn_str = os.environ.get('AZURE_COMMUNICATION_CONNECTION_STRING', '')
            sender = os.environ.get('ACS_EMAIL_SENDER', '')
            if not conn_str or not sender:
                logger.error('ACS email not configured')
                return False

            client = EmailClient.from_connection_string(conn_str)
            message = {
                'senderAddress': sender,
                'recipients': {'to': [{'address': to_email}]},
                'content': {'subject': subject, 'html': html},
            }
            poller = client.begin_send(message)
            poller.result()
            return True
        except Exception:
            logger.exception('ACS email send failed to %s', _mask_email(to_email))
            return False

    def send_password_reset(self, to_email: str, reset_link: str, user_name: str) -> bool:
        return self._send(to_email, 'Reset your GenBI password',
                          _reset_email_html(user_name, reset_link))

    def send_otp(self, to_email: str, otp_code: str, user_name: str) -> bool:
        return self._send(to_email, 'Your GenBI verification code',
                          _otp_email_html(user_name, otp_code))


# ── Factory ──────────────────────────────────────────────────────────────────

def get_email_provider() -> EmailProvider:
    provider = os.environ.get('EMAIL_PROVIDER', 'console').lower()
    if provider == 'gmail':
        return GmailApiEmailProvider()
    elif provider == 'smtp':
        return SmtpEmailProvider()
    elif provider == 'acs':
        return AcsEmailProvider()
    return ConsoleEmailProvider()


# ── Shared email templates ───────────────────────────────────────────────────

def _reset_email_html(user_name: str, reset_link: str) -> str:
    safe_name = html_escape(user_name)
    return f"""
    <div style="font-family:'Inter',system-ui,sans-serif;max-width:480px;margin:0 auto;padding:40px 24px;color:#1a1a2e">
      <h2 style="margin:0 0 16px;font-size:22px">Reset your password</h2>
      <p style="color:#555;line-height:1.6;margin:0 0 24px">
        Hi {safe_name},<br><br>
        We received a request to reset your GenBI password.
        Click the button below to choose a new password. This link expires in 30 minutes.
      </p>
      <a href="{html_escape(reset_link)}"
         style="display:inline-block;padding:12px 32px;background:#00e5ff;color:#000;
                font-weight:600;text-decoration:none;border-radius:9999px;font-size:14px">
        Reset Password
      </a>
      <p style="color:#999;font-size:12px;margin:32px 0 0;line-height:1.5">
        If you didn't request this, you can safely ignore this email.
        <br>— The GenBI Team
      </p>
    </div>
    """


def _otp_email_html(user_name: str, otp_code: str) -> str:
    safe_name = html_escape(user_name)
    safe_otp = html_escape(otp_code)
    return f"""
    <div style="font-family:'Inter',system-ui,sans-serif;max-width:480px;margin:0 auto;padding:40px 24px;color:#1a1a2e">
      <h2 style="margin:0 0 16px;font-size:22px">Verify your email</h2>
      <p style="color:#555;line-height:1.6;margin:0 0 24px">
        Hi {safe_name},<br><br>
        Use the code below to verify your GenBI account. This code expires in 10 minutes.
      </p>
      <div style="background:#f4f4f4;border-radius:12px;padding:20px;text-align:center;margin:0 0 24px">
        <span style="font-size:36px;font-weight:700;letter-spacing:8px;color:#1a1a2e">{safe_otp}</span>
      </div>
      <p style="color:#999;font-size:12px;margin:0;line-height:1.5">
        If you didn't create an account, you can safely ignore this email.
        <br>— The GenBI Team
      </p>
    </div>
    """
