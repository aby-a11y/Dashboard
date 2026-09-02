(function () {
  var token = localStorage.getItem('admin_token');
  if (!token) {
    window.location.href = '/admin-login';
    return;
  }

  // This script only ever loads on the admin panel (index.html) — never on
  // Clientview.html or shared.html — so it's safe to attach the admin token
  // to every /api/... call it makes: /api/admin/... (client/workflow/account
  // management) AND the plain /api/sites, /api/summary, /api/ga4/... etc.
  // (the actual GSC/GA4 data the dashboard displays). Both kinds now require
  // a valid admin token on the backend — see main.py.
  var originalFetch = window.fetch;
  window.fetch = function (url, options) {
    options = options || {};
    var urlStr = typeof url === 'string' ? url : url.url;
    var isApiCall = urlStr && urlStr.indexOf('/api/') !== -1;
    var isLogin = urlStr && urlStr.indexOf('/api/admin/login') !== -1;
    if (isApiCall && !isLogin) {
      options.headers = Object.assign({}, options.headers, {
        'Authorization': 'Bearer ' + localStorage.getItem('admin_token')
      });
    }
    return originalFetch(url, options).then(function (res) {
      if (res.status === 401 && isApiCall && !isLogin) {
        localStorage.removeItem('admin_token');
        window.location.href = '/admin-login';
      }
      return res;
    });
  };
})();