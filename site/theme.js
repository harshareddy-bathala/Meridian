/* Meridian — theme resolution.
 *
 * Loaded in <head> WITHOUT defer and BEFORE style.css, so data-theme is on the
 * root element before the first paint. That ordering is the whole point: defer
 * would run this after parsing and the page would flash the wrong theme.
 *
 * The usual fix for that flash is three lines of inline <script> in the head.
 * The CSP in _headers allows no 'unsafe-inline', so this is a file instead —
 * one extra same-origin request, and the no-third-party-requests claim stays
 * enforced rather than asserted.
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
