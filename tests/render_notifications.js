/* Exercise the notification state helper from the browser bundle with a tiny
 * DOM shim. This deliberately evaluates the real source instead of copying the
 * transition logic into a test implementation. */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const script = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');
const start = script.indexOf("const NOTIFICATION_PREFERENCE");
const end = script.indexOf('// ------------------------------------------------------------- navigation', start);
assert(start >= 0 && end > start, 'notification source block not found');
const notificationSource = script.slice(start, end);

function environment({ permission = 'granted', preference = 'disabled', focused = false,
                       supported = true } = {}) {
  const handlers = {};
  const toasts = [];
  const systemNotices = [];
  const navigation = [];
  let focusCalls = 0;

  const toggle = {
    disabled: false,
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, handler) { handlers[name] = handler; },
  };
  const status = { textContent: '' };
  const toastRegion = { appendChild(node) { toasts.push(node.textContent); } };
  const elements = {
    'notification-toggle': toggle,
    'notification-status': status,
    'toast-region': toastRegion,
  };
  const values = new Map([['pdf2docx-desktop-notifications', preference]]);
  const context = {
    Map, Set,
    TERMINAL: new Set(['done', 'error', 'cancelled']),
    $: id => elements[id],
    localStorage: {
      getItem: key => values.get(key) || null,
      setItem: (key, value) => values.set(key, value),
    },
    document: {
      hidden: !focused,
      hasFocus: () => focused,
      createElement: () => ({ className: '', textContent: '', remove() {} }),
    },
    window: { focus() { focusCalls += 1; } },
    showDashboard: target => navigation.push(target),
    setTimeout() {},
  };
  if (supported) {
    function BrowserNotification(title, options) {
      this.title = title;
      this.body = options.body;
      this.close = () => {};
      systemNotices.push(this);
    }
    BrowserNotification.permission = permission;
    BrowserNotification.requestPermission = async () => BrowserNotification.permission;
    context.Notification = BrowserNotification;
  }
  vm.createContext(context);
  vm.runInContext(notificationSource + `
    globalThis.notificationTestApi = {
      observeJobStates, batchCompletionSummary, batchNoticeStates,
      showCompletionNotice, syncNotificationControl
    };`, context);
  return {
    api: context.notificationTestApi,
    handlers, toasts, systemNotices, navigation, toggle, status, values,
    focusCalls: () => focusCalls,
  };
}

async function run() {
  const state = environment({ preference: 'enabled' });
  state.api.observeJobStates([
    { batch_id: 'old', status: 'done' },
    { batch_id: 'old', status: 'error' },
  ], { seed: true });
  assert.deepStrictEqual(state.toasts, [], 'finished initial history must stay quiet');

  state.api.observeJobStates([
    { batch_id: 'new', status: 'running' },
    { batch_id: 'new', status: 'queued' },
  ]);
  const finished = [
    { batch_id: 'new', status: 'done' },
    { batch_id: 'new', status: 'error' },
  ];
  state.api.observeJobStates(finished);
  state.api.observeJobStates(finished); // duplicate history/batch polling observation
  assert.deepStrictEqual(state.toasts, ['Batch finished: 1 completed, 1 failed.']);
  assert.strictEqual(state.systemNotices.length, 1);
  state.systemNotices[0].onclick();
  assert.strictEqual(state.focusCalls(), 1);
  assert.deepStrictEqual(state.navigation, ['history']);

  const focused = environment({ focused: true });
  focused.api.observeJobStates([{ batch_id: 'b', status: 'ready' }]);
  focused.api.observeJobStates([{ batch_id: 'b', status: 'cancelled' }]);
  assert.deepStrictEqual(focused.toasts, ['Batch finished: 1 cancelled.']);
  assert.strictEqual(focused.systemNotices.length, 0, 'a focused tab only needs its toast');

  const denied = environment({ permission: 'denied', preference: 'enabled' });
  assert.strictEqual(denied.toggle.disabled, true);
  assert.strictEqual(denied.status.textContent, 'Blocked in browser settings');

  const unsupported = environment({ supported: false });
  assert.strictEqual(unsupported.toggle.disabled, true);
  assert.strictEqual(unsupported.status.textContent, 'Not supported by this browser');
}

run().catch(error => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
