# routes/auth.py
import hmac

from flask import Blueprint, current_app, jsonify, request, session
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash
from services.account_recovery import (
    credential_version, recovery_available, serializer, send_reset_email,
)

from extensions import db
from models import User
from services.logger import get_logger
from services.rate_limit import SharedRateLimiter
from services.validation import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
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
INVALID_CREDENTIALS = 'The email or password is incorrect. Please try again.'


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


def _error(message, status=400, error_type="validation", field=None):
    payload = {"success": False, "error": message, "error_type": error_type}
    if field:
        payload["field"] = field
    return jsonify(payload), status


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


@auth_bp.before_app_request
def check_credential_version():
    # Changing a password invalidates every older login, not just the reset link.
    if request.path.startswith("/api/") and "user_id" in session:
        user = db.session.get(User, session["user_id"])
        stored = session.get("credential_version", "")
        if user is None or not isinstance(stored, str) or not hmac.compare_digest(stored, credential_version(user)):
            session.clear()


@auth_bp.route('/api/auth/status', methods=['GET'])
def auth_status():
    payload = {"isLoggedIn": False, "passwordResetAvailable": recovery_available()}
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
        if user:
            payload.update(isLoggedIn=True, email=user.email)
    return jsonify(payload)


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

    data = _body()
    if data is None:
        return _error("Send an email address and password.", error_type="invalid_request")
    try:
        if not isinstance(data.get("email"), str):
            raise ValidationError("Enter a valid email address.")
        email = validate_email(data.get('email'))
    except ValidationError as exc:
        return _error(str(exc), field="email")
    try:
        if not isinstance(data.get("password"), str):
            raise ValidationError("Enter a password.")
        password = validate_password(data.get('password'))
    except ValidationError as exc:
        return _error(str(exc), field="password")

    if User.query.filter_by(email=email).first():
        return jsonify({
            'success': False,
            'error': 'An account already uses this email. Sign in or reset your password.',
            'error_type': 'account_exists',
        }), 409

    new_user = User(email=email)
    new_user.set_password(password)
    db.session.add(new_user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _error("An account already uses this email. Sign in or reset your password.", 409, "account_exists")
    logger.info('Account created')

    return jsonify({'success': True, 'message': 'Account created! Please login.'})


@auth_bp.route('/api/login', methods=['POST'])
def login():
    data = _body()
    if data is None:
        return _error("Send an email address and password.", error_type="invalid_request")
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
    if (not isinstance(data.get("email"), str) or len(data["email"]) > MAX_EMAIL_LENGTH
            or not raw_email or not isinstance(password, str) or not password
            or len(password) > MAX_PASSWORD_LENGTH):
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
    session['credential_version'] = credential_version(user)
    session.permanent = True
    return jsonify({'success': True, 'email': user.email})


@auth_bp.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@auth_bp.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    limited = _throttle("recovery-ip", _client_address(), 5, 3600)
    if limited:
        return limited
    if not recovery_available():
        return _error("Password recovery is not configured on this installation.", 503, "recovery_unavailable")
    data = _body()
    if data is None:
        return _error("Enter your email address.", field="email")
    try:
        if not isinstance(data.get("email"), str):
            raise ValidationError("Enter a valid email address.")
        email = validate_email(data["email"])
    except ValidationError as exc:
        return _error(str(exc), field="email")
    # Account-specific limiting also bounds mail across multiple source IPs.
    limited = _throttle("recovery-email", email, 3, 3600)
    if limited:
        return limited
    user = User.query.filter_by(email=email).first()
    if user:
        try:
            send_reset_email(user)
        except Exception:
            # Do not disclose account existence, token, SMTP credentials or PII.
            logger.error("Password recovery email delivery failed; inspect the mail service")
    return jsonify(success=True, message="If the account exists, a reset link will be sent.")


@auth_bp.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    limited = _throttle("reset-ip", _client_address(), 10, 3600)
    if limited:
        return limited
    data = _body()
    if data is None or not isinstance(data.get("token"), str) or len(data["token"]) > 2048:
        return _error("This reset link is invalid or expired. Request a new link.", error_type="invalid_token")
    try:
        payload = serializer().loads(data["token"], max_age=1800)
    except (BadSignature, SignatureExpired):
        return _error("This reset link is invalid or expired. Request a new link.", error_type="invalid_token")
    if not isinstance(payload, dict) or not isinstance(payload.get("user_id"), int) or not isinstance(payload.get("version"), str):
        return _error("This reset link is invalid or expired. Request a new link.", error_type="invalid_token")
    user = db.session.get(User, payload["user_id"])
    if not user or not hmac.compare_digest(payload["version"], credential_version(user)):
        return _error("This reset link is invalid or expired. Request a new link.", error_type="invalid_token")
    try:
        if not isinstance(data.get("password"), str):
            raise ValidationError("Enter a new password.")
        password = validate_password(data["password"])
    except ValidationError as exc:
        return _error(str(exc), field="password")
    # Compare-and-swap makes a token single-use even for simultaneous requests.
    changed = db.session.query(User).filter_by(id=user.id, password_hash=user.password_hash).update(
        {"password_hash": generate_password_hash(password)}, synchronize_session=False)
    db.session.commit()
    if not changed:
        return _error("This reset link has already been used. Request a new link.", error_type="invalid_token")
    session.clear()
    return jsonify(success=True, message="Password updated. Please sign in again.")
