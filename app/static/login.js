/* The sign-in page. One form, two modes: an invite code is the only difference.
   Kept separate from app.js so nothing the workspace needs loads before there is
   an account to load it for. */

const $ = (id) => document.getElementById(id);

let mode = 'signin';
let signupOpen = false;

function show(element, visible) {
  element.classList.toggle('hidden', !visible);
}

function fail(message) {
  const box = $('auth-error');
  box.textContent = message;
  show(box, true);
}

function setMode(next) {
  mode = next;
  const creating = mode === 'signup';

  $('tab-signin').setAttribute('aria-selected', String(!creating));
  $('tab-signup').setAttribute('aria-selected', String(creating));
  show($('invite-field'), creating);
  show($('auth-error'), false);

  $('password').autocomplete = creating ? 'new-password' : 'current-password';
  $('auth-hint').textContent = creating
    ? 'Use at least 8 characters. An invite code is required.'
    : '';
  show($('auth-hint'), creating);
  $('auth-submit').innerHTML = creating
    ? 'Create account <span aria-hidden="true">→</span>'
    : 'Sign in <span aria-hidden="true">→</span>';
}

async function submit(event) {
  event.preventDefault();
  show($('auth-error'), false);

  const button = $('auth-submit');
  button.disabled = true;
  try {
    const body = { email: $('email').value, password: $('password').value };
    if (mode === 'signup') body.invite_code = $('invite-code').value;

    const response = await fetch(`/api/auth/${mode === 'signup' ? 'signup' : 'login'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      fail(detail.detail || 'That did not work. Try again.');
      return;
    }
    location.href = '/';
  } catch (error) {
    fail('Could not reach the server.');
  } finally {
    button.disabled = false;
  }
}

async function start() {
  $('auth-form').addEventListener('submit', submit);
  $('tab-signin').addEventListener('click', () => setMode('signin'));
  $('tab-signup').addEventListener('click', () => setMode('signup'));

  try {
    const config = await (await fetch('/api/auth/config')).json();
    signupOpen = Boolean(config.signup_open);
  } catch (_) {
    signupOpen = false;
  }

  // With no invite code configured there is no account to create, so the tab
  // that would only ever fail is not offered.
  show($('tab-signup'), signupOpen);
  show($('signup-closed'), !signupOpen);
  setMode('signin');
}

start();
