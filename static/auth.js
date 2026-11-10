/* 票据续带：页面靠地址上的 ?k= 进来，页面内部的接口也必须带上它。
 *
 * 为什么必须有这个文件：
 *   飞书把面板塞在 iframe 里，第三方 cookie 被浏览器拦掉，所以「靠 cookie 认人」在飞书里根本不成立。
 *   页面能打开，是因为 URL 上带了 k；但页面里的 fetch('/api/...') 不带 k、又没有 cookie，
 *   服务端只能回 401「未登录或登录已过期」——人看到的就是这句，跟权限无关。
 *   （顶层浏览器有 cookie，所以这个 bug 在桌面测不出来，只会在飞书里炸。）
 *
 * 做法：放在业务脚本之前引，给页面里所有同源 fetch 自动补 k，不用改每个调用点，
 *      以后新加接口也不会再漏。跨域请求一律不动（不要把票据发到别人域名上）。
 */
(function () {
  var K = '';
  try { K = new URLSearchParams(location.search).get('k') || ''; } catch (e) { K = ''; }
  window.QC_K = K;

  // 给任意 URL 续带票据；已带 k 的、跨域的原样返回
  window.qcWithK = function (url) {
    var s = String(url == null ? '' : url);
    if (!K || !s) return url;
    if (/([?&])k=/.test(s)) return url;
    if (/^[a-z][a-z0-9+.-]*:/i.test(s) && s.indexOf(location.origin) !== 0) return url;  // http(s)/blob/data 非同源
    return s + (s.indexOf('?') < 0 ? '?' : '&') + 'k=' + encodeURIComponent(K);
  };

  /* 在飞书框架里、地址又没有票据：cookie 也指望不上（会被拦），再往下只会一片 401。
     回面板去认人 —— 面板会向宿主要 open_id 换票据，认完就能正常点进来。
     只对「嵌在别人框架里」生效，顶层浏览器照旧走 cookie，不打扰。 */
  if (!K) {
    var inFrame = false;
    try { inFrame = window.top !== window.self; } catch (e) { inFrame = true; }
    var p = location.pathname;
    if (inFrame && p !== '/panel' && p !== '/login' && p !== '/') {
      location.replace('/panel');
    }
    return;
  }

  var raw = window.fetch;
  window.fetch = function (input, init) {
    try {
      if (typeof input === 'string') {
        input = window.qcWithK(input);
      } else if (input && typeof input === 'object' && typeof input.url === 'string') {
        var u = window.qcWithK(input.url);
        if (u !== input.url) {
          input = new Request(u, {
            method: input.method, headers: input.headers, body: input.body,
            mode: input.mode, credentials: input.credentials, cache: input.cache,
            redirect: input.redirect, referrer: input.referrer, integrity: input.integrity
          });
        }
      }
    } catch (e) { /* 补不上就照原样发，让服务端回 401，别把请求吞掉 */ }
    return raw.call(this, input, init);
  };

  // 票据真的过期时，别只丢一句「未登录或登录已过期」——告诉人下一步该干什么
  window.qcExplain401 = function (r) {
    if (r && r.login) {
      r.error = '这个链接已失效（或账号被停用）。回飞书重新打开面板即可。';
    }
    return r;
  };
})();
