import { readFileSync } from 'fs';
import { resolve } from 'path';
import { vi, beforeAll, beforeEach, afterEach, describe, test, expect } from 'vitest';

beforeAll(() => {
  // Load dependencies first
  window.eval(readFileSync(resolve('./quadletman/static/src/metrics.js'), 'utf-8'));
  // Provide t() stub
  window.QM_I18N = {};
  window.eval('function t(key) { return (window.QM_I18N && window.QM_I18N[key]) || key; }');
  // Load polling.js
  window.eval(readFileSync(resolve('./quadletman/static/src/polling.js'), 'utf-8'));
});

describe('renderStatusBadges', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="status-comp1" class="flex flex-wrap gap-2 min-h-[1.5rem]"></div>';
  });

  test('renders active badge with green dot', () => {
    window.renderStatusBadges('comp1', [
      { container: 'web', active_state: 'active', sub_state: 'running', load_state: 'loaded', unit_file_state: 'enabled' },
    ]);
    const el = document.getElementById('status-comp1');
    expect(el.innerHTML).toContain('qm-dot-green');
    expect(el.innerHTML).toContain('running');
    expect(el.innerHTML).toContain('web');
    // autostart enabled icon
    expect(el.innerHTML).toContain('\u23FB');
    expect(el.innerHTML).toContain('qm-autostart-on');
  });

  test('renders failed badge with red dot', () => {
    window.renderStatusBadges('comp1', [
      { container: 'db', active_state: 'failed', sub_state: 'failed', load_state: 'loaded', unit_file_state: 'disabled' },
    ]);
    const el = document.getElementById('status-comp1');
    expect(el.innerHTML).toContain('qm-dot-danger');
    expect(el.innerHTML).toContain('failed');
    expect(el.innerHTML).toContain('qm-autostart-off');
  });

  test('renders transitioning badge with yellow dot', () => {
    window.renderStatusBadges('comp1', [
      { container: 'app', active_state: 'activating', sub_state: '', load_state: 'loaded', unit_file_state: '' },
    ]);
    const el = document.getElementById('status-comp1');
    expect(el.innerHTML).toContain('qm-dot-warn');
    expect(el.innerHTML).toContain('activating');
  });

  test('renders not-found badge', () => {
    window.renderStatusBadges('comp1', [
      { container: 'x', active_state: 'inactive', sub_state: '', load_state: 'not-found', unit_file_state: '' },
    ]);
    const el = document.getElementById('status-comp1');
    expect(el.innerHTML).toContain('qm-dot-loading');
    expect(el.innerHTML).toContain('not loaded');
  });

  test('renders unknown badge', () => {
    window.renderStatusBadges('comp1', [
      { container: 'x', active_state: 'unknown', sub_state: '', load_state: '', unit_file_state: '' },
    ]);
    const el = document.getElementById('status-comp1');
    expect(el.innerHTML).toContain('qm-dot-loading');
    expect(el.innerHTML).toContain('qm-opacity-0');
    expect(el.innerHTML).toContain('unknown');
  });

  test('renders fetching placeholder for empty statuses', () => {
    window.renderStatusBadges('comp1', []);
    const el = document.getElementById('status-comp1');
    expect(el.innerHTML).toContain('animate-pulse');
    expect(el.innerHTML).toContain('fetching');
    expect(el.innerHTML).toContain('qm-dot-sm');
  });

  test('no-op when target element missing', () => {
    document.body.innerHTML = '';
    expect(() => window.renderStatusBadges('missing', [])).not.toThrow();
  });

  test('escapes HTML in container names', () => {
    window.renderStatusBadges('comp1', [
      { container: '<script>alert(1)</script>', active_state: 'active', sub_state: 'running', load_state: 'loaded', unit_file_state: '' },
    ]);
    const el = document.getElementById('status-comp1');
    // Text content is escaped via _esc
    expect(el.innerHTML).toContain('&lt;script&gt;');
    // Attribute value is escaped via _escAttr — no raw < in onclick
    expect(el.innerHTML).toContain('\\x3cscript\\x3e');
  });
});

describe('renderStatusDots', () => {
  beforeEach(() => {
    document.body.innerHTML =
      '<span id="cmp-dot-a" class="qm-dot qm-dot-loading inline-block"></span>' +
      '<span id="cmp-dot-b" class="qm-dot qm-dot-loading inline-block"></span>';
  });

  test('updates dot color and title', () => {
    window.renderStatusDots([
      { compartment_id: 'a', color: 'bg-green-500', title: 'all running' },
      { compartment_id: 'b', color: 'bg-red-500', title: '1 failed' },
    ]);
    const a = document.getElementById('cmp-dot-a');
    expect(a.className).toContain('qm-dot');
    expect(a.className).toContain('bg-green-500');
    expect(a.title).toBe('all running');
    const b = document.getElementById('cmp-dot-b');
    expect(b.className).toContain('bg-red-500');
    expect(b.title).toBe('1 failed');
  });

  test('no-op for null input', () => {
    expect(() => window.renderStatusDots(null)).not.toThrow();
  });
});

describe('ViewPoller', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Stub htmx event listeners
    document.body.innerHTML = '<div id="main-content"></div>';
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test('calls onData on first tick', async () => {
    const onData = vi.fn();
    const onDisk = vi.fn();
    const mockData = {
      poll_interval: 5,
      disk_poll_interval: 60,
      metrics: [],
      status_dots: [],
      disk: [{ compartment_id: 'a', disk_bytes: 100 }],
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      type: 'default',
      status: 200,
      json: () => Promise.resolve(mockData),
    });

    const poller = window.ViewPoller({ url: '/api/dashboard/poll', onData, onDisk });
    poller.start();
    // Wait for the async _poll() to complete
    await vi.advanceTimersByTimeAsync(0);

    expect(onData).toHaveBeenCalledWith(mockData);
    // First tick always includes disk
    expect(onDisk).toHaveBeenCalledWith(mockData.disk);
    // URL should include include_disk on first tick
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('include_disk=true'),
      expect.any(Object),
    );
    poller.stop();
  });

  test('stop prevents further polls', async () => {
    const onData = vi.fn();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      type: 'default',
      status: 200,
      json: () => Promise.resolve({ poll_interval: 5, disk_poll_interval: 60, metrics: [], status_dots: [], disk: null }),
    });

    const poller = window.ViewPoller({ url: '/test', onData });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    onData.mockClear();
    globalThis.fetch.mockClear();

    await vi.advanceTimersByTimeAsync(10000);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  test('redirects to login on 401', async () => {
    const onData = vi.fn();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      type: 'default',
      status: 401,
    });
    // Mock location
    delete window.location;
    window.location = { href: '' };

    const poller = window.ViewPoller({ url: '/test', onData });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(window.location.href).toBe('/login');
    expect(onData).not.toHaveBeenCalled();
  });
});

describe('_esc', () => {
  test('escapes HTML entities', () => {
    expect(window._esc('<b>"hello"&</b>')).toBe('&lt;b&gt;&quot;hello&quot;&amp;&lt;/b&gt;');
  });

  test('returns empty string for falsy input', () => {
    expect(window._esc('')).toBe('');
    expect(window._esc(null)).toBe('');
    expect(window._esc(undefined)).toBe('');
  });
});
