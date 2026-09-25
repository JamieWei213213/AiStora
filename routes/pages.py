# routes/pages.py
from flask import Blueprint, redirect, render_template

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
