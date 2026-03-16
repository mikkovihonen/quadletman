import { readFileSync } from 'fs';
import { resolve } from 'path';

beforeAll(() => {
  window.eval(readFileSync(resolve('./quadletman/static/src/metrics.js'), 'utf-8'));
});

describe('fmtBytes', () => {
  test('bytes', () => expect(window.fmtBytes(500)).toBe('500 B'));
  test('kilobytes', () => expect(window.fmtBytes(1500)).toBe('1.5 KB'));
  test('megabytes', () => expect(window.fmtBytes(2.5e6)).toBe('2.5 MB'));
  test('gigabytes', () => expect(window.fmtBytes(1.5e9)).toBe('1.5 GB'));
  test('boundary: exactly 1 KB', () => expect(window.fmtBytes(1000)).toBe('1.0 KB'));
  test('boundary: exactly 1 MB', () => expect(window.fmtBytes(1e6)).toBe('1.0 MB'));
  test('boundary: exactly 1 GB', () => expect(window.fmtBytes(1e9)).toBe('1.0 GB'));
});

describe('setText', () => {
  test('updates element text when element exists', () => {
    document.body.innerHTML = '<span id="target">old</span>';
    window.setText('target', 'new');
    expect(document.getElementById('target').textContent).toBe('new');
  });

  test('is a no-op when element does not exist', () => {
    // Should not throw
    expect(() => window.setText('nonexistent', 'value')).not.toThrow();
  });
});
