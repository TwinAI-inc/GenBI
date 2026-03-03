"""
Helper script to get a Gmail API refresh token for sending emails.

Usage:
    python3 get_gmail_token.py

You'll be prompted for your Client ID and Client Secret from Google Cloud Console,
then a browser will open for you to authorize the app.
"""

import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

# Google OAuth2 endpoints
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REDIRECT_URI = 'http://localhost:8090'
SCOPE = 'https://www.googleapis.com/auth/gmail.send'

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)
        auth_code = query.get('code', [None])[0]

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        if auth_code:
            self.wfile.write(b'<h2>Authorization successful! You can close this tab.</h2>')
        else:
            error = query.get('error', ['unknown'])[0]
            self.wfile.write(f'<h2>Authorization failed: {error}</h2>'.encode())

    def log_message(self, format, *args):
        pass  # suppress logs


def main():
    print('=' * 50)
    print('  Gmail API — Refresh Token Setup')
    print('=' * 50)
    print()

    client_id = input('Enter your Client ID: ').strip()
    client_secret = input('Enter your Client Secret: ').strip()

    if not client_id or not client_secret:
        print('Error: Both Client ID and Client Secret are required.')
        return

    # Build authorization URL
    params = {
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',
    }
    auth_url = f'{AUTH_URL}?{urlencode(params)}'

    print()
    print('Opening browser for authorization...')
    print(f'(If it does not open, visit: {auth_url})')
    webbrowser.open(auth_url)

    # Wait for the callback
    server = HTTPServer(('localhost', 8090), CallbackHandler)
    server.handle_request()

    if not auth_code:
        print('Error: Did not receive authorization code.')
        return

    print('Got authorization code, exchanging for refresh token...')

    # Exchange code for tokens
    import urllib.request
    import ssl
    token_data = urlencode({
        'code': auth_code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code',
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=token_data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    # macOS Python often lacks default SSL certs — try certifi first, fall back
    ssl_ctx = None
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ssl_ctx) as resp:
            tokens = json.loads(resp.read())
    except Exception as e:
        print(f'Error exchanging code: {e}')
        return

    refresh_token = tokens.get('refresh_token')
    if not refresh_token:
        print('Error: No refresh token in response. Try again with prompt=consent.')
        print(f'Response: {json.dumps(tokens, indent=2)}')
        return

    # Write secrets directly to .env file instead of printing to stdout
    env_lines = (
        f'EMAIL_PROVIDER=gmail\n'
        f'GMAIL_CLIENT_ID={client_id}\n'
        f'GMAIL_CLIENT_SECRET={client_secret}\n'
        f'GMAIL_REFRESH_TOKEN={refresh_token}\n'
        f'GMAIL_SENDER=your-gmail@gmail.com\n'
    )

    env_path = os.path.join(os.path.dirname(__file__), '.env')
    try:
        with open(env_path, 'a') as f:
            f.write('\n# Gmail API credentials (auto-generated)\n')
            f.write(env_lines)
        os.chmod(env_path, 0o600)
        print()
        print('=' * 50)
        print('  SUCCESS! Credentials appended to .env')
        print('=' * 50)
        print()
        print(f'  GMAIL_CLIENT_ID={client_id[:12]}...')
        print(f'  GMAIL_CLIENT_SECRET=****')
        print(f'  GMAIL_REFRESH_TOKEN=****')
        print()
        print('Edit .env to set GMAIL_SENDER to your Gmail address.')
    except IOError as e:
        print(f'Could not write to {env_path}: {e}')
        print('Manually add these lines to your .env file:')
        print(env_lines)


if __name__ == '__main__':
    main()
