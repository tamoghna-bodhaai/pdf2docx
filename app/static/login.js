/* The sign-in page. An email and a password; an invite code is what separates
   signing in from creating an account.
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

/* A 200 from the sign-in endpoint is not the same as being signed in: the
   browser can accept the response and still discard the cookie that came with
   it — a `Secure` cookie over plain HTTP is dropped without a word. Redirecting
   on the status alone is what turned that into a login page that bounces you
   back to itself with nothing on screen to say why. So ask who we are, and only
   leave once the server agrees it is someone. */
async function enter() {
  const response = await fetch('/api/auth/me', { credentials: 'same-origin' });
  if (!response.ok) {
    fail(
      'Signed in, but your browser did not keep the session cookie. If you are '
      + 'reaching this over plain HTTP, use HTTPS or set PDF2DOCX_COOKIE_SECURE=off.'
    );
    return;
  }
  location.href = '/';
}

async function post(url, body, button) {
  button.disabled = true;
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      fail(detail.detail || 'That did not work. Try again.');
      return;
    }
    await enter();
  } catch (error) {
    fail('Could not reach the server.');
  } finally {
    button.disabled = false;
  }
}

async function submit(event) {
  event.preventDefault();
  show($('auth-error'), false);

  const body = { email: $('email').value, password: $('password').value };
  if (mode === 'signup') body.invite_code = $('invite-code').value;

  await post(`/api/auth/${mode === 'signup' ? 'signup' : 'login'}`, body, $('auth-submit'));
}


/* Something upstream can still redirect here with a reason attached — a
   session that expired mid-request has no response body for anyone to read. */
function readQuery() {
  const params = new URLSearchParams(location.search);
  if (params.get('error')) fail(params.get('error'));
  // Leave the address bar clean, so a reload does not resurrect a stale error.
  if (params.has('error')) {
    history.replaceState(null, '', location.pathname);
  }
}

async function start() {
  $('auth-form').addEventListener('submit', submit);
  $('tab-signin').addEventListener('click', () => setMode('signin'));
  $('tab-signup').addEventListener('click', () => setMode('signup'));

  let config = {};
  try {
    config = await (await fetch('/api/auth/config', { credentials: 'same-origin' })).json();
  } catch (_) {
    config = {};
  }
  signupOpen = Boolean(config.signup_open);

  readQuery();

  // With no invite code configured there is no account to create, so the tab
  // that would only ever fail is not offered.
  show($('tab-signup'), signupOpen);
  show($('signup-closed'), !signupOpen);
  setMode('signin');
}

start();
