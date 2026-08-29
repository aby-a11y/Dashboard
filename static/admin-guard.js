(function () {
  var token = localStorage.getItem('admin_token');
  if (!token) {
    window.location.href = '/admin-login';
    return;
  }

  var originalFetch = window.fetch;
  window.fetch = function (url, options) {
    options = options || {};
    var urlStr = typeof url === 'string' ? url : url.url;
    if (urlStr && urlStr.indexOf('/api/admin/') !== -1 && urlStr.indexOf('/api/admin/login') === -1) {
      options.headers = Object.assign({}, options.headers, {
        'Authorization': 'Bearer ' + localStorage.getItem('admin_token')
      });
    }
    return originalFetch(url, options).then(function (res) {
      if (res.status === 401 && urlStr && urlStr.indexOf('/api/admin/') !== -1) {
        localStorage.removeItem('admin_token');
        window.location.href = '/admin-login';
      }
      return res;
    });
  };
})();