/* v12660 静态script探测：验证内网EDR是否净化普通<script>加载的JS。
   若页面自检区显示「静态script探测: YES」→ 静态路径不被净化，可走静态直发方案；
   若显示 FAIL → 静态script也被净化，只剩EDR白名单一条路。 */
window.__staticProbeOK = 'YES' + '_' + new Date().getTime();
