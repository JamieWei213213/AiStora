# routes/auth.py
from flask import Blueprint, current_app, jsonify, request, session

from extensions import db
from models import User
from services.logger import get_logger
from services.rate_limit import SharedRateLimiter
from services.validation import (
    MAX_EMAIL_LENGTH,
    ValidationError,
    validate_email,
    validate_password,
)

auth_bp = Blueprint('auth', __name__)
logger = get_logger(__name__)

# Credential endpoints were previously unthrottled, so an attacker could try
# passwords as fast as the server would answer. Limits are applied per client
# address and per targeted account, so one account cannot be hammered from many
# addresses and one address cannot spray many accounts.
auth_rate_limiter = SharedRateLimiter('auth')

LOGIN_ATTEMPTS_PER_MINUTE = 10
REGISTRATIONS_PER_HOUR = 5

# Returned for both "no such user" and "wrong password" so the endpoint does
# not disclose which email addresses have accounts.
INVALID_CREDENTIALS = 'Invalid email or password'


def rotate_session_id():
    """Issue a new server-side session id, discarding the old record.

    Must run *before* ``session.clear()``: Flask-Session's ``regenerate()``
    is a no-op on an empty session, which is exactly the state ``clear()``
    leaves behind, so the previous ordering rotated nothing.
    """
    interface = current_app.session_interface
    regenerate = getattr(interface, "regenerate", None)
    if callable(regenerate) and session:
        regenerate(session)
        return
    generate = getattr(interface, "_generate_sid", None)
    if callable(generate) and hasattr(session, "sid"):
        session.sid = generate(getattr(interface, "sid_length", 32))
        session.modified = True


def _client_address():
    return request.remote_addr or 'unknown'


def _throttle(scope, key, limit, window_seconds):
    allowed, retry_after = auth_rate_limiter.check(
        f'{scope}:{key}',
        limit=limit,
        window_seconds=window_seconds,
    )
    if allowed:
        return None
    response = jsonify({
        'success': False,
        'error': 'Too many attempts. Please wait and try again.',
        'error_type': 'rate_limit',
    })
    response.headers['Retry-After'] = str(retry_after)
    return response, 429


@auth_bp.route('/api/auth/status', methods=['GET'])
def auth_status():
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
        if user is None:
            # The account was deleted while the session was still alive.
            session.clear()
            return jsonify({'isLoggedIn': False})
        return jsonify({'isLoggedIn': True, 'email': user.email})
    return jsonify({'isLoggedIn': False})


@auth_bp.route('/api/register', methods=['POST'])
def register():
    limited = _throttle(
        'register',
        _client_address(),
        int(current_app.config.get('REGISTRATIONS_PER_HOUR', REGISTRATIONS_PER_HOUR)),
        3600,
    )
    if limited:
        return limited

    data = request.get_json(silent=True) or {}
    try:
        email = validate_email(data.get('email'))
        password = validate_password(data.get('password'))
    except ValidationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({
            'success': False,
            'error': 'Email already registered',
        }), 409

    new_user = User(email=email)
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    logger.info('Account created')

    return jsonify({'success': True, 'message': 'Account created! Please login.'})


@auth_bp.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    # Bounded *before* it is used as a limiter key. This string used to be
    # taken verbatim from the request body, so a 50 MB "email" became a
    # 50 MB dictionary key that was never released.
    raw_email = str(data.get('email') or '')[:MAX_EMAIL_LENGTH].strip().casefold()
    limit = int(current_app.config.get(
        'LOGIN_ATTEMPTS_PER_MINUTE', LOGIN_ATTEMPTS_PER_MINUTE))

    for scope, key in (('login-ip', _client_address()), ('login-user', raw_email)):
        if not key:
            continue
        limited = _throttle(scope, key, limit, 60)
        if limited:
            return limited

    password = data.get('password')
    if not raw_email or not password:
        return jsonify({'success': False, 'error': INVALID_CREDENTIALS}), 401

    user = User.query.filter_by(email=raw_email).first()
    if not user or not user.check_password(password):
        logger.info('Failed login attempt')
        return jsonify({'success': False, 'error': INVALID_CREDENTIALS}), 401

    # A new session identifier is issued on privilege change so that a session
    # fixed before login cannot be reused afterwards. ``session.clear()`` alone
    # keeps the same server-side id, so the interface's regenerate() is used
    # when the backend provides it (Flask-Session 0.8 does).
    rotate_session_id()
    session.clear()
    session['user_id'] = user.id
    session['user_email'] = user.email
    session.permanent = True
    return jsonify({'success': True, 'email': user.email})


@auth_bp.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})
