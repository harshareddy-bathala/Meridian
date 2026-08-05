/* Meridian — theme resolution, and the gate on the home page's intro.
 *
 * Loaded in <head> WITHOUT defer and BEFORE style.css, so data-theme is on the
 * root element before the first paint. That ordering is the whole point: defer
 * would run this after parsing and the page would flash the wrong theme.
 *
 * The usual fix for that flash is three lines of inline <script> in the head.
 * The CSP in _headers allows no 'unsafe-inline', so this is a file instead —
 * one extra same-origin request, and the no-third-party-requests claim stays
 * enforced rather than asserted.
 *
 * The intro gate lives here for the same reason: it has to be decided before
 * the first paint, and this is the only script that runs that early. It is a
 * few lines and it is on every page, but it does nothing at all unless the
 * document asks for it with data-intro. See D-041.
 */

'use strict';

(function () {
  var KEY = 'meridian-theme';
  var root = document.documentElement;
  var media = window.matchMedia('(prefers-color-scheme: dark)');

  /* Storage throws rather than returning null in some privacy modes, so every
     access is guarded. A visitor who blocks storage still gets a working
     toggle for the length of the page view; it just does not persist. */
  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) {
      return null;
    }
  }

  function apply(theme) {
    root.dataset.theme = theme;
    document.dispatchEvent(new CustomEvent('themechange', { detail: theme }));
  }

  function system() {
    return media.matches ? 'dark' : 'light';
  }

  apply(stored() || system());

  /* Follow the operating system only while the visitor has expressed no
     preference of their own. Once they have, their choice outranks it. */
  media.addEventListener('change', function () {
    if (!stored()) apply(system());
  });

  /* ---------------------------------------------------------- intro gate --
   * The home page's content is hidden for 4.2 s while the globe resolves.
   * That delay is a CSS animation, and it used to run unconditionally — the
   * stylesheet only ever *cancelled* it once main.js reported in.
   *
   * The comment there claimed that was the resilient arrangement. It was the
   * inverse: when main.js could not run — blocked by a shield, a stale cache
   * serving a mismatched module, a browser with no 2D canvas — the visitor
   * waited the full 4.2 s at a blank page and then got no globe either. The
   * failure mode was strictly worse than having no intro at all.
   *
   * So the delay is now opt-in. This adds `intro` before the first paint, and
   * removes it again unless main.js confirms it has painted. Nothing about the
   * animation itself changed; only what happens when it cannot start.
   */
  if (root.hasAttribute('data-intro')) gateIntro();

  function canDrawCanvas() {
    try {
      return !!document.createElement('canvas').getContext('2d');
    } catch (e) {
      return false;
    }
  }

  function gateIntro() {
    /* Reduced motion has no intro to wait for, and a browser that cannot give
       us a 2D context will never paint a globe. Both skip straight to visible
       content rather than sitting through a delay for something that is not
       coming. */
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (!canDrawCanvas()) return;

    root.classList.add('intro');

    /* main.js is a deferred module: parse, fetch, import orbit.js, first draw.
       On a fast connection that is well under 200 ms. If nothing has reported
       in by the time below, something has gone wrong that this page cannot see
       — so drop the gate and show the content rather than honouring a delay on
       behalf of an animation that is not running. */
    window.setTimeout(function () {
      if (!root.classList.contains('intro-ready')) root.classList.remove('intro');
    }, 900);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var button = document.getElementById('theme-toggle');
    if (!button) return;

    function relabel() {
      var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      button.setAttribute('aria-label', 'Switch to ' + next + ' theme');
      button.setAttribute('title', 'Switch to ' + next + ' theme');
    }

    /* Revealed only once JS is running. Its slot is sized in CSS whether or
       not it is shown, so unhiding it shifts nothing — and a visitor without
       JS never sees a control that could not do anything. */
    button.hidden = false;
    relabel();

    button.addEventListener('click', function () {
      var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(KEY, next);
      } catch (e) {
        /* Not fatal. The theme still changes for this page view. */
      }
      apply(next);
      relabel();
    });
  });
}());
