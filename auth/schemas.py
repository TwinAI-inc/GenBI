"""
Request validation helpers.
"""

import re

MIN_PASSWORD_LENGTH = 8


def validate_signup(data: dict) -> list[str]:
    errors = []
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not name or len(name) < 2:
        errors.append('Name must be at least 2 characters.')
    if not email or not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        errors.append('A valid email address is required.')
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f'Password must be at least {MIN_PASSWORD_LENGTH} characters.')
    if password and not re.search(r'[A-Za-z]', password):
        errors.append('Password must contain at least one letter.')
    if password and not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one number.')

    return errors


def validate_login(data: dict) -> list[str]:
    errors = []
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''

    if not email:
        errors.append('Email is required.')
    if not password:
        errors.append('Password is required.')

    return errors


def validate_reset_password(data: dict) -> list[str]:
    errors = []
    token = (data.get('token') or '').strip()
    password = data.get('new_password') or ''

    if not token:
        errors.append('Reset token is required.')
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f'Password must be at least {MIN_PASSWORD_LENGTH} characters.')
    if password and not re.search(r'[A-Za-z]', password):
        errors.append('Password must contain at least one letter.')
    if password and not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one number.')

    return errors


def validate_otp(data: dict) -> list[str]:
    errors = []
    otp = (data.get('otp') or '').strip()
    if not otp or not re.match(r'^\d{6}$', otp):
        errors.append('A valid 6-digit verification code is required.')
    return errors


def validate_set_password(data: dict) -> list[str]:
    errors = []
    password = data.get('password') or ''

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f'Password must be at least {MIN_PASSWORD_LENGTH} characters.')
    if password and not re.search(r'[A-Za-z]', password):
        errors.append('Password must contain at least one letter.')
    if password and not re.search(r'[0-9]', password):
        errors.append('Password must contain at least one number.')

    return errors
