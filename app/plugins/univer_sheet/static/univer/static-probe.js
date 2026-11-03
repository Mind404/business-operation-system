/* v12669: ES2023 polyfill for old browsers (must run before Univer engine) */
(function() {
  if (!Array.prototype.toSorted) {
    Array.prototype.toSorted = function(compareFn) {
      return this.slice().sort(compareFn);
    };
  }
  if (!Array.prototype.findLast) {
    Array.prototype.findLast = function(predicate) {
      for (var i = this.length - 1; i >= 0; i--) {
        if (predicate(this[i], i, this)) return this[i];
      }
      return undefined;
    };
  }
  if (!Array.prototype.findLastIndex) {
    Array.prototype.findLastIndex = function(predicate) {
      for (var i = this.length - 1; i >= 0; i--) {
        if (predicate(this[i], i, this)) return i;
      }
      return -1;
    };
  }
  if (!Array.prototype.at) {
    Array.prototype.at = function(n) {
      n = Math.trunc(n) || 0;
      if (n < 0) n += this.length;
      if (n < 0 || n >= this.length) return undefined;
      return this[n];
    };
  }
  if (!Object.hasOwn) {
    Object.hasOwn = function(obj, prop) {
      return Object.prototype.hasOwnProperty.call(obj, prop);
    };
  }
  console.log('[polyfill] toSorted ready:', typeof Array.prototype.toSorted);
})();

/* v12660 static probe: check if EDR purges normal <script> loaded JS */
window.__staticProbeOK = 'YES' + '_' + new Date().getTime();