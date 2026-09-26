# routes/pages.py
from flask import Blueprint, current_app, redirect, render_template

pages_bp = Blueprint('pages', __name__)

AUTH_TITLES = {'login': 'Sign in | AIStora', 'signup': 'Create your account | AIStora'}


@pages_bp.route('/')
def index():
    return render_template('app.html')


@pages_bp.route('/app')
def app_page():
    return render_template('app.html')


@pages_bp.route('/login')
def login_page():
    """Dedicated sign-in page; the SPA reads the path to pick its mode."""
    return render_template('app.html', page_title=AUTH_TITLES['login'])


@pages_bp.route('/signup')
def signup_page():
    """Dedicated account-creation page (linked from the sign-in page)."""
    return render_template('app.html', page_title=AUTH_TITLES['signup'])


@pages_bp.route('/register')
def register_redirect():
    return redirect('/signup', code=301)


def _limits():
    config = current_app.config
    return {
        "max_upload_mb": max(1, int(config.get("MAX_CONTENT_LENGTH", 5 * 1024 * 1024) or 0) // (1024 * 1024)),
        "max_tables": int(config.get("MAX_TABLES_PER_PROJECT", 20) or 0),
        "max_databases": int(config.get("MAX_PROJECTS_PER_USER", 10) or 0),
        "ai_per_day": int(config.get("AGENT_DAILY_REQUESTS_PER_USER", 150) or 0),
    }


@pages_bp.route('/guide')
def guide_page():
    """Plain-HTML user guide; also linked from the sign-in page and the app."""
    return render_template(
        'guide.html',
        limits=_limits(),
        support_email=current_app.config.get("SUPPORT_EMAIL", "jamiejwei@gmail.com"),
    )
