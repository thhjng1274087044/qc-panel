/* 「返回首页」统一行为 —— 页面里放 <a data-home>返回首页</a> 就行。
 *
 * 只做一件事：href 统一成 `/`，并把当前地址上的票据 k 原样带走。
 *
 * 为什么只改 href、不加 target=_top：
 *   面板是嵌在飞书里的 iframe。target=_top 会把顶层窗口顶掉，
 *   飞书客户端会当成外部链接另开一个全屏 webview —— 人就离开飞书界面了（实测被骂过一次）。
 *   留在 iframe 里能回得去，靠的是「首页也认票据」：
 *   `/?k=<票据>` 会让首页带着票据去问 /api/me，不依赖被拦掉的第三方 cookie。
 *
 * 站内互跳同理：`<a data-nav="/review">核对台</a>`，票据照样带走。
 *   不要在模板里写 href="/review" —— 飞书 iframe 里没票据会被弹回登录。
 *
 * 样式不动，按钮长什么样由各页自己的 .hd a 决定。
 */
(function () {
  var k = '';
  try { k = new URLSearchParams(location.search).get('k') || ''; } catch (e) {}
  var q = k ? '?k=' + encodeURIComponent(k) : '';
  document.querySelectorAll('[data-home]').forEach(function (a) {
    a.setAttribute('href', '/' + q);
  });
  document.querySelectorAll('[data-nav]').forEach(function (a) {
    var p = a.getAttribute('data-nav') || '/';
    // data-nav 里可以自带查询串（如 /review?rec=xxx），票据接在后面
    if (!k) { a.setAttribute('href', p); return; }
    a.setAttribute('href', p + (p.indexOf('?') >= 0 ? '&' : '?') + 'k=' + encodeURIComponent(k));
  });
})();
