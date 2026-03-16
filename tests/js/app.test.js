import { readFileSync } from 'fs';
import { resolve } from 'path';

beforeAll(() => {
  // app.js registers a DOMContentLoaded handler that accesses DOM elements.
  // In jsdom, DOMContentLoaded has already fired by the time tests run, so
  // the handler is never invoked — only the top-level function declarations are evaluated.
  window.eval(readFileSync(resolve('./quadletman/static/src/app.js'), 'utf-8'));
});

describe('t() i18n helper', () => {
  afterEach(() => {
    delete window.QM_I18N;
  });

  test('returns the key when no translation map is set', () => {
    expect(window.t('Hello')).toBe('Hello');
  });

  test('returns translated string when key exists', () => {
    window.QM_I18N = { Hello: 'Hei' };
    expect(window.t('Hello')).toBe('Hei');
  });

  test('falls back to key when key is missing from map', () => {
    window.QM_I18N = { Other: 'Muu' };
    expect(window.t('Hello')).toBe('Hello');
  });
});

describe('chmodEditor octal getter', () => {
  const allOff = { ur: false, uw: false, ux: false, gr: false, gw: false, gx: false, or: false, ow: false, ox: false };
  const allOn  = { ur: true,  uw: true,  ux: true,  gr: true,  gw: true,  gx: true,  or: true,  ow: true,  ox: true  };

  test('all permissions off → 000', () => {
    expect(window.chmodEditor(allOff).octal).toBe('000');
  });

  test('all permissions on → 777', () => {
    expect(window.chmodEditor(allOn).octal).toBe('777');
  });

  test('owner read+write only → 600', () => {
    expect(window.chmodEditor({ ...allOff, ur: true, uw: true }).octal).toBe('600');
  });

  test('owner rwx, group rx, other none → 750', () => {
    expect(window.chmodEditor({ ...allOff, ur: true, uw: true, ux: true, gr: true, gx: true }).octal).toBe('750');
  });

  test('typical file 644', () => {
    expect(window.chmodEditor({ ...allOff, ur: true, uw: true, gr: true, or: true }).octal).toBe('644');
  });
});
