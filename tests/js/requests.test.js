import { readFileSync } from 'fs';
import { resolve } from 'path';

beforeAll(() => {
  // requests.js registers htmx and HTMX event listeners — these are harmless in jsdom.
  window.eval(readFileSync(resolve('./quadletman/static/src/requests.js'), 'utf-8'));
});

afterEach(() => {
  // Clear all cookies between tests
  document.cookie.split(';').forEach(c => {
    document.cookie = c.trim().split('=')[0] + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/';
  });
});

describe('getCsrfToken', () => {
  test('returns empty string when cookie is absent', () => {
    expect(window.getCsrfToken()).toBe('');
  });

  test('returns token value when qm_csrf cookie is present', () => {
    document.cookie = 'qm_csrf=abc123';
    expect(window.getCsrfToken()).toBe('abc123');
  });

  test('returns token when multiple cookies are set', () => {
    document.cookie = 'other=foo';
    document.cookie = 'qm_csrf=mytoken';
    expect(window.getCsrfToken()).toBe('mytoken');
  });

  test('URL-decodes the token value', () => {
    document.cookie = 'qm_csrf=tok%2Fwith%2Fslashes';
    expect(window.getCsrfToken()).toBe('tok/with/slashes');
  });
});
